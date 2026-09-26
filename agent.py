import os
import json

from dotenv import load_dotenv
from openai import OpenAI

from tools import (
    search_menu,
    add_to_order,
    remove_from_order,
    get_order
)


load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


# --------------------------------------------------
# TOOLS AVAILABLE TO THE MODEL
# --------------------------------------------------

tools = [
    {
        "type": "function",
        "name": "search_menu",
        "description": "Search the restaurant menu for food, drinks, menu items or prices.",
        "parameters": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "The food or drink the customer is searching for."
                }
            },
            "required": ["search_term"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "add_to_order",
        "description": "Add a confirmed menu item to the customer's current order.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The exact menu item ID, such as B002."
                },
                "quantity": {
                    "type": "integer",
                    "description": "Number of this item to add."
                }
            },
            "required": ["item_id", "quantity"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "remove_from_order",
        "description": "Remove a quantity of an item from the customer's current order.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The exact menu item ID."
                },
                "quantity": {
                    "type": "integer",
                    "description": "Number of this item to remove."
                }
            },
            "required": ["item_id", "quantity"],
            "additionalProperties": False
        }
    },
    {
        "type": "function",
        "name": "get_order",
        "description": "Get the customer's current order and total price.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False
        }
    }
]


# --------------------------------------------------
# TOOL EXECUTION
# --------------------------------------------------

def execute_tool(name, arguments):

    if name == "search_menu":
        return search_menu(
            arguments["search_term"]
        )

    if name == "add_to_order":
        return add_to_order(
            arguments["item_id"],
            arguments["quantity"]
        )

    if name == "remove_from_order":
        return remove_from_order(
            arguments["item_id"],
            arguments["quantity"]
        )

    if name == "get_order":
        return get_order()

    return {
        "error": f"Unknown tool: {name}"
    }


# --------------------------------------------------
# AGENT
# --------------------------------------------------

def run_agent(user_message, previous_response_id=None):

    request = {
        "model": "gpt-5.4-mini",
        "instructions": """
        You are the AI receptionist for OrderAI Burger House.

        Your job is to help customers browse the menu and build
        their restaurant order.

        Use search_menu whenever you need menu information.

        Never invent menu items, prices or item IDs.

        Before adding an item to an order, make sure you know the
        correct item ID from menu information available in the
        conversation or by using search_menu.

        Use add_to_order when the customer clearly asks to add or
        order an item.

        Use remove_from_order when the customer asks to remove an
        item.

        Use get_order when the customer asks what they have ordered,
        asks for their total, or wants an order summary.

        All prices are in GBP and must use the £ symbol.
        Before changing the customer's order, the requested item must be
        unambiguous.

        If the customer's request could refer to more than one item currently
        in their order, do not guess based on the most recent conversation.

        Use get_order to inspect the current order when necessary.

        If multiple items could match the customer's request, ask the customer
        which specific item they mean before calling add_to_order or
        remove_from_order.

        Only perform the action once the intended item is clear.
        """,
        "input": user_message,
        "tools": tools
    }

    if previous_response_id:
        request["previous_response_id"] = previous_response_id

    response = client.responses.create(**request)

    # Allow the agent to use several tools if necessary
    while True:

        tool_calls = [
            item
            for item in response.output
            if item.type == "function_call"
        ]

        if not tool_calls:
            return response.output_text, response.id

        tool_outputs = []

        for tool_call in tool_calls:

            arguments = json.loads(tool_call.arguments)

            result = execute_tool(
                tool_call.name,
                arguments
            )

            tool_outputs.append({
                "type": "function_call_output",
                "call_id": tool_call.call_id,
                "output": json.dumps(result)
            })

        response = client.responses.create(
            model="gpt-5.4-mini",
            instructions="""
            You are the AI receptionist for OrderAI Burger House.

            Continue helping the customer using the tool results.

            Never invent menu items, prices or item IDs.
            All prices are in GBP and must use the £ symbol.
            """,
            previous_response_id=response.id,
            input=tool_outputs,
            tools=tools
        )