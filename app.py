from orderai.core.agent import run_agent


previous_response_id = None

print("OrderAI Burger House")
print("Type 'exit' to end the conversation.\n")


while True:

    question = input("Customer: ")

    if question.lower() == "exit":
        print("OrderAI: Thanks! Goodbye.")
        break

    answer, previous_response_id = run_agent(
        question,
        previous_response_id
    )

    print(f"OrderAI: {answer}\n")
    