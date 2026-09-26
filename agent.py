import os
import json

from dotenv import load_dotenv
from openai import OpenAI

from tools import (
    search_menu,
    resolve_menu_item,
    add_item_to_order,
    continue_pending_action,
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
        "name": "add_item_to_order",
        "description": """
        Safely add a menu item to the customer's order.
        This tool resolves the customer's description before changing
        the order and will refuse to add an ambiguous or unknown item.
        """,
        "parameters": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "The customer's description of the menu item."
                },
                "quantity": {
                    "type": "integer",
                    "description": "Number of this item to add."
                }
            },
            "required": ["search_term", "quantity"],
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
    },
    {
        "type": "function",
        "name": "resolve_menu_item",
        "description": """
        Resolve a customer's description of a menu item before changing
        the order. Returns resolved if exactly one menu item matches,
        ambiguous if multiple items match, or not_found if none match.
        """,
        "parameters": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "The customer's description of the menu item."
                }
            },
            "required": ["search_term"],
            "additionalProperties": False
        }
    },
    {
    "type": "function",
    "name": "continue_pending_action",
    "description": """
    Continue an unfinished order action after the customer provides
    clarification about which item they mean.
    """,
    "parameters": {
        "type": "object",
        "properties": {
            "clarification": {
                "type": "string",
                "description": "The customer's clarification, such as beef or spicy chicken."
            }
        },
        "required": ["clarification"],
        "additionalProperties": False
    }
},
]


# --------------------------------------------------
# TOOL EXECUTION
# --------------------------------------------------

def execute_tool(name, arguments):

    if name == "search_menu":
        return search_menu(
            arguments["search_term"]
        )

    if name == "add_item_to_order":
        return add_item_to_order(
            arguments["search_term"],
            arguments["quantity"]
        )

    if name == "remove_from_order":
        return remove_from_order(
            arguments["item_id"],
            arguments["quantity"]
        )

    if name == "get_order":
        return get_order()
    
    if name == "resolve_menu_item":
        return resolve_menu_item(
        arguments["search_term"]
    )
    
    if name == "continue_pending_action":
        return continue_pending_action(
        arguments["clarification"]
    )

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
        
        Use add_item_to_order when the customer asks to add or order
        a menu item.

        The tool validates the customer's description before changing
        the order.

        If the tool returns "ambiguous", ask the customer which of the
        matching items they mean.

        If the tool returns "not_found", tell the customer the requested
        item could not be found.
        
        Never choose one of multiple matches yourself.
        
        If an add request was previously ambiguous and you asked the customer
        to clarify which item they meant, use continue_pending_action with
        their clarification.

        Do not create a new add request when the customer is answering a
        clarification question about an unfinished add request.

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