/* Browser WebRTC with backend-owned order state. Also exported for offline tests. */
"use strict";
// Keep Web IDL timer functions bound to their browser receiver.
function browserTimers(host) {
  return {
    every: (callback, delay) => host.setInterval(callback, delay),
    cancelEvery: id => host.clearInterval(id),
    after: (callback, delay) => host.setTimeout(callback, delay),
    cancelAfter: id => host.clearTimeout(id)
  };
}
class DemoCallController {
  constructor(deps) {
    Object.assign(this, deps);
    this.state = "idle";
    this.generation = 0;
    this.callId = null;
    this.revision = -1;
    this.peer = null;
    this.channel = null;
    this.stream = null;
    this.timer = null;
    this.deadline = null;
    this.readyTimer = null;
    this.queue = Promise.resolve();
    this.batches = new Map();
    this.toolJobs = new Map();
    this.recovering = false;
  }
  setState(state, error = "") {
    this.state = state;
    this.view({state, error});
  }
  current(generation) { return generation === this.generation; }
  applySnapshot(data) {
    if (data.call_id !== this.callId || data.revision < this.revision) return;
    this.revision = data.revision;
    this.view({snapshot: data});
  }
  async restore() {
    const previous = this.storage.getItem("orderai-demo-call");
    if (!previous) return;
    this.recovering = true;
    this.setState("ending");
    this.callId = previous;
    try {
      const data = await this.api(`/demo/calls/${previous}`);
      this.applySnapshot(data);
      if (data.call_status === "active") {
        this.applySnapshot(await this.api(`/demo/calls/${previous}/end`, {}));
      }
      this.setState("ended");
    } catch (error) {
      this.storage.removeItem("orderai-demo-call");
      this.callId = null;
      this.setState("error", error.message);
    } finally { this.recovering = false; }
  }
  async start(accessCode) {
    if (this.recovering || !["idle", "ended", "error"].includes(this.state)) return;
    if (!accessCode.trim()) { this.setState("error", "Enter your demonstration access code."); return; }
    const generation = ++this.generation;
    this.callId = null;
    this.revision = -1;
    this.batches.clear();
    this.toolJobs.clear();
    this.queue = Promise.resolve();
    this.setState("requesting_permission");
    try {
      const stream = await this.media.getUserMedia({audio: true});
      if (!this.current(generation)) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream;
      this.setState("connecting");
      const data = await this.api("/demo/calls", {access_code: accessCode});
      if (!this.current(generation)) {
        await this.api(`/demo/calls/${data.call_id}/end`, {});
        return;
      }
      this.callId = data.call_id;
      this.storage.setItem("orderai-demo-call", this.callId);
      this.applySnapshot(data);
      this.timer = this.every(() => this.heartbeat(generation), 10000);
      this.deadline = this.after(() => {
        if (this.current(generation)) this.fail("The demonstration call time limit was reached.");
      }, 600000);
      const credential = await this.api("/realtime-session", {call_id: this.callId});
      if (!this.current(generation)) return;
      const peer = this.makePeer();
      this.peer = peer;
      peer.ontrack = event => {
        if (!this.current(generation)) return;
        Promise.resolve(this.play(event.streams[0])).catch(() => {
          if (this.current(generation)) this.fail("Audio playback was blocked. Start the call again.");
        });
      };
      peer.onconnectionstatechange = () => {
        if (this.current(generation) && ["failed", "disconnected", "closed"].includes(peer.connectionState)) {
          this.fail("Voice connection was interrupted. Start a new call to continue.");
        }
      };
      stream.getTracks().forEach(track => peer.addTrack(track, stream));
      const channel = peer.createDataChannel("oai-events");
      this.channel = channel;
      channel.onclose = () => { if (this.current(generation)) this.fail("Voice connection closed."); };
      channel.onerror = () => { if (this.current(generation)) this.fail("Voice connection failed."); };
      channel.onmessage = event => {
        if (!this.current(generation)) return;
        try { this.event(JSON.parse(event.data), generation); }
        catch (_) { this.fail("An invalid voice event was received."); }
      };
      const offer = await peer.createOffer();
      if (!this.current(generation)) return;
      await peer.setLocalDescription(offer);
      const response = await this.sdp(offer.sdp, credential.value);
      if (!this.current(generation)) return;
      await peer.setRemoteDescription({type: "answer", sdp: response});
      if (!this.current(generation)) return;
      // Backend sets tools and instructions when minting the credential.
      // Wait for session.created and an open channel before marking ready.
      channel.onopen = () => { if (this.current(generation) && this.sessionReady) this.setState("listening"); };
      if (channel.readyState === "open" && this.sessionReady) this.setState("listening");
      if (!this.sessionReady) this.readyTimer = this.after(() => {
        if (this.current(generation) && !this.sessionReady) this.fail("The voice service did not become ready. Please try a new call.");
      }, 15000);
    } catch (error) {
      if (this.current(generation)) await this.fail(error.name === "NotAllowedError"
        ? "Microphone permission was denied. Allow microphone access, then try again."
        : error.message || "Could not start the call.");
    }
  }
  event(message, generation) {
    if (!this.current(generation)) return;
    if (message.type === "error") { this.fail("The voice service reported an error. Please start a new call."); return; }
    if (message.type === "session.created" || message.type === "session.updated") {
      this.sessionReady = true;
      if (this.readyTimer !== null) this.cancelAfter(this.readyTimer);
      this.readyTimer = null;
      if (this.channel?.readyState === "open") this.setState("listening");
    }
    if (message.type === "input_audio_buffer.speech_started") this.setState("listening");
    if (message.type === "input_audio_buffer.speech_stopped") this.setState("processing");
    if (message.type === "output_audio_buffer.started") this.setState("speaking");
    if (["output_audio_buffer.stopped", "output_audio_buffer.cleared"].includes(message.type)) this.setState("listening");
    if (message.type === "response.output_item.done" && message.item?.type === "function_call") {
      const batch = this.batch(message.response_id);
      const item = message.item;
      if (this.toolJobs.has(item.call_id)) return;
      batch.pending++;
      batch.hasTools = true;
      this.queue = this.queue.then(async () => {
        if (!this.current(generation)) return;
        this.setState("processing");
        const result = await this.api("/realtime-tool", {
          call_id: this.callId, tool_call_id: item.call_id,
          name: item.name, arguments: JSON.parse(item.arguments)
        });
        if (!this.current(generation)) return;
        this.applySnapshot(result);
        this.send({type: "conversation.item.create", item: {
          type: "function_call_output", call_id: item.call_id, output: JSON.stringify(result.result)
        }});
      }).catch(error => {
        if (this.current(generation)) {
          if (error.data?.order) this.applySnapshot(error.data);
          return this.fail(error.message || "Could not synchronise the order.");
        }
      }).finally(() => {
        batch.pending--;
        if (this.current(generation)) this.continueBatch(batch);
      });
      this.toolJobs.set(item.call_id, this.queue);
    }
    if (message.type === "response.done") {
      const batch = this.batch(message.response?.id);
      batch.done = true;
      this.continueBatch(batch);
    }
  }
  batch(id) {
    if (!this.batches.has(id)) this.batches.set(id, {pending: 0, done: false, hasTools: false, continued: false});
    return this.batches.get(id);
  }
  continueBatch(batch) {
    if (batch.done && batch.hasTools && batch.pending === 0 && !batch.continued) {
      batch.continued = true;
      this.send({type: "response.create"});
    }
  }
  send(message) {
    if (this.channel?.readyState === "open") this.channel.send(JSON.stringify(message));
  }
  async heartbeat(generation) {
    if (!this.current(generation) || !this.callId) return;
    try {
      const data = await this.api(`/demo/calls/${this.callId}/heartbeat`, {});
      if (this.current(generation)) this.applySnapshot(data);
    } catch (error) { if (this.current(generation)) await this.fail(error.message); }
  }
  cleanup() {
    this.sessionReady = false;
    if (this.timer !== null) this.cancelEvery(this.timer);
    if (this.deadline !== null) this.cancelAfter(this.deadline);
    if (this.readyTimer !== null) this.cancelAfter(this.readyTimer);
    this.timer = this.deadline = null;
    this.readyTimer = null;
    if (this.channel) { this.channel.onclose = null; this.channel.onerror = null; this.channel.close(); }
    if (this.peer) { this.peer.onconnectionstatechange = null; this.peer.close(); }
    if (this.stream) this.stream.getTracks().forEach(track => track.stop());
    this.peer = this.channel = this.stream = null;
    this.play(null);
  }
  async end(error = "") {
    const identity = this.callId;
    const generation = ++this.generation;
    this.setState("ending");
    this.cleanup();
    try {
      if (identity) {
        const data = await this.api(`/demo/calls/${identity}/end`, {});
        if (this.current(generation)) this.applySnapshot(data);
      }
    } catch (failure) { error = error || `${failure.message} The microphone is off; the backend call will expire.`; }
    if (this.current(generation)) this.setState(error ? "error" : "ended", error);
  }
  fail(message) { return this.end(message); }
  unload() {
    ++this.generation;
    this.cleanup();
    if (this.callId) this.keepalive(`/demo/calls/${this.callId}/end`);
  }
}

if (typeof module !== "undefined") module.exports = {DemoCallController, browserTimers};
if (typeof document !== "undefined") {
  // Category jumps stay inside the menu window, leaving its controls in place.
  const menuItems = document.querySelector(".menu-items");
  const menuPanel = document.querySelector(".menu-panel");
  if (menuPanel) {
    const sizeMenuWindow = () => {
      // Use the document position so page scrolling cannot change the panel height.
      const top = menuPanel.getBoundingClientRect().top + window.scrollY;
      menuPanel.style.setProperty("--menu-top", `${top}px`);
    };
    sizeMenuWindow();
    window.addEventListener("resize", sizeMenuWindow);
    if (typeof ResizeObserver !== "undefined") {
      const headerObserver = new ResizeObserver(sizeMenuWindow);
      document.querySelectorAll(".site-header, .demo-notice").forEach(element => headerObserver.observe(element));
    }
  }
  document.querySelector(".category-nav")?.addEventListener("click", event => {
    const link = event.target.closest("a");
    if (!link || !menuItems || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const category = document.getElementById(link.hash.slice(1));
    if (!category || !menuItems.contains(category)) return;
    event.preventDefault();
    menuItems.scrollTo({top: menuItems.scrollTop + category.getBoundingClientRect().top
      - menuItems.getBoundingClientRect().top - 4});
    menuItems.focus({preventScroll: true});
  });
  const csrf = document.querySelector('meta[name="demo-csrf"]').content;
  const start = document.getElementById("start-call");
  const end = document.getElementById("end-call");
  const code = document.getElementById("access-code");
  const audio = document.getElementById("realtime-audio");
  const labels = {idle: "Ready to start", requesting_permission: "Requesting microphone permission…",
    connecting: "Connecting…", listening: "Listening", processing: "Processing your request…",
    speaking: "Nova is speaking", ending: "Ending call…", ended: "Call ended", error: "Call unavailable"};
  const formatter = new Intl.NumberFormat("en-GB", {style: "currency", currency: "GBP"});
  let announcedRevision = -1;
  function view(update) {
    if (update.state) {
      document.getElementById("call-status").textContent = labels[update.state];
      document.querySelector(".call-indicator").dataset.state = update.state;
      const inactive = ["idle", "ended", "error"].includes(update.state);
      start.disabled = code.disabled || !inactive;
      end.disabled = inactive || update.state === "ending";
      const error = document.getElementById("call-error");
      error.textContent = update.error || "";
      error.hidden = !update.error;
    }
    if (!update.snapshot) return;
    const data = update.snapshot;
    const list = document.getElementById("cart-items");
    list.replaceChildren();
    let count = 0;
    for (const item of data.order.items) {
      count += item.quantity;
      const row = document.createElement("li"); row.className = "cart-item";
      const text = document.createElement("div");
      text.textContent = `${item.quantity} × ${item.name}`;
      const unit = document.createElement("small"); unit.textContent = `${formatter.format(item.price)} each`; text.append(unit);
      for (const modifier of item.modifiers || []) {
        const note = document.createElement("small"); note.textContent = `${modifier.type}: ${modifier.ingredient}`; text.append(note);
      }
      const amount = document.createElement("strong"); amount.textContent = formatter.format(item.line_total);
      row.append(text, amount); list.append(row);
    }
    document.getElementById("cart-empty").hidden = count !== 0;
    document.getElementById("cart-count").textContent = `${count} ${count === 1 ? "item" : "items"}`;
    document.getElementById("cart-total").textContent = formatter.format(data.order.total);
    const statuses = {building: "Building your demonstration order", awaiting_confirmation: "Ready for your confirmation",
      confirmed: "Confirmed for demonstration", submitted: "Demo completed — no real order was sent"};
    document.getElementById("order-status").textContent = statuses[data.order.status] || data.order.status;
    const feedback = document.getElementById("order-feedback");
    const pending = data.pending_action?.action;
    feedback.textContent = pending ? "That request needs clarification. No item has been added yet."
      : (data.result?.message || (["not_found", "item_not_found", "ambiguous"].includes(data.result?.status)
        ? "The request could not be completed. Please clarify the item." : ""));
    feedback.hidden = !feedback.textContent;
    if (data.revision !== announcedRevision) {
      document.getElementById("cart-announcement").textContent = `Order updated. ${count} items. Total ${formatter.format(data.order.total)}.`;
      announcedRevision = data.revision;
    }
  }
  async function api(path, body) {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 30000);
    try {
      const response = await fetch(path, {method: body === undefined ? "GET" : "POST", cache: "no-store",
        headers: {"Content-Type": "application/json", "X-Demo-CSRF": csrf},
        body: body === undefined ? undefined : JSON.stringify(body), signal: abort.signal});
      const data = await response.json();
      if (!response.ok) { const error = new Error(data.error || data.result?.message || "Request failed."); error.data = data; throw error; }
      return data;
    } finally { clearTimeout(timer); }
  }
  const controller = new DemoCallController({api, view, storage: sessionStorage,
    media: {getUserMedia: constraints => {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("Microphone access requires a supported browser on localhost or HTTPS.");
      return navigator.mediaDevices.getUserMedia(constraints);
    }}, makePeer: () => new RTCPeerConnection(),
    play: stream => { audio.srcObject = stream; return stream ? audio.play() : undefined; },
    sdp: async (offer, token) => {
      const response = await fetch("https://api.openai.com/v1/realtime/calls", {method: "POST", body: offer,
        headers: {Authorization: `Bearer ${token}`, "Content-Type": "application/sdp"}, signal: AbortSignal.timeout(25000)});
      if (!response.ok) throw new Error("Voice connection could not be established.");
      return response.text();
    }, ...browserTimers(window),
    keepalive: path => { fetch(path, {method: "POST", keepalive: true,
      headers: {"X-Demo-CSRF": csrf}}).catch(() => {}); }
  });
  start.addEventListener("click", () => { const value = code.value; code.value = ""; controller.start(value); });
  end.addEventListener("click", () => controller.end());
  window.addEventListener("pagehide", () => controller.unload());
  window.addEventListener("pageshow", event => { if (event.persisted) controller.restore(); });
  controller.restore();
}
