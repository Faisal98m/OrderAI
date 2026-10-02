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
    get_order,
    modify_order_item,
    customize_order_item,
    customize_order_line,
    validate_order, 
    confirm_order,
    submit_order,
    start_new_order

    
    
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
        "description": """
            Remove an entire menu item or quantity of that item from the customer's
            order. Use this only when the customer wants to remove the whole item,
            for example 'remove my burger' or 'take one Coke off my order'.

            Do not use this tool for ingredient changes such as 'no lettuce',
            'remove onions', or 'without sauce'. Use modify_order_item for those.
        """,
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
    {
            "type": "function",
            "name": "modify_order_item",
            "description": """
            Modify an item already in the customer's order by removing an ingredient.

            Use this for ingredient-level requests such as:
            'no lettuce',
            'remove the onions',
            'without jalapenos',
            'no sauce on my burger'.

            This changes the ingredients of an order item. It does not remove
            the whole menu item from the order.
            """,
            "parameters": {
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "The order item the customer wants to modify."
                    },
                    "ingredient": {
                        "type": "string",
                        "description": "The ingredient the customer wants removed."
                    }
                },
                "required": ["search_term", "ingredient"],
                "additionalProperties": False
            }
},
    {
    "type": "function",
    "name": "customize_order_item",
    "description": """
    Remove an ingredient from some or all of a menu item already in the
    customer's order.

    Use this when the customer specifies how many items should receive
    the modification, for example:
    'one burger with no lettuce',
    'remove onions from two of my burgers',
    'only one of the classic chicken burgers should have no lettuce'.

    The tool safely handles splitting order lines when only part of a
    quantity should be modified.
    """,
    "parameters": {
        "type": "object",
        "properties": {
            "search_term": {
                "type": "string",
                "description": "The order item the customer wants to modify."
            },
            "ingredient": {
                "type": "string",
                "description": "The ingredient to remove."
            },
            "quantity": {
                "type": "integer",
                "description": "How many of the item should receive this modification."
            }
        },
        "required": [
            "search_term",
            "ingredient",
            "quantity"
        ],
        "additionalProperties": False
    }
},
    {
    "type": "function",
    "name": "customize_order_line",
    "description": """
    Modify a specific existing order line after the correct line has
    already been identified from the customer's current order.

    Use this when multiple order lines contain the same menu item but
    have different configurations, such as one Classic Chicken Burger
    with no lettuce and four standard Classic Chicken Burgers.

    Use the exact line_id from the current order. Do not invent a line_id.
    """,
    "parameters": {
        "type": "object",
        "properties": {
            "line_id": {
                "type": "string",
                "description": "The exact line_id of the order line to modify."
            },
            "ingredient": {
                "type": "string",
                "description": "The ingredient to remove."
            },
            "quantity": {
                "type": "integer",
                "description": "How many items from this order line should receive the modification."
            }
        },
        "required": [
            "line_id",
            "ingredient",
            "quantity"
        ],
        "additionalProperties": False
    }
},
    {
    "type": "function",
    "name": "validate_order",
    "description": """
    Validate the customer's current order when they indicate they are
    finished ordering and want to proceed.

    This checks whether the order is ready to be presented for final
    confirmation. It does not confirm or submit the order.
    """,
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    }
},
{
    "type": "function",
    "name": "confirm_order",
    "description": """
    Confirm the customer's order only after the final order has been
    validated and presented to the customer, and the customer explicitly
    confirms that they want to proceed.

    This does not submit the order.
    """,
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    }
},
{
    "type": "function",
    "name": "submit_order",
    "description": """
    Submit an order that has already been explicitly confirmed.

    This tool must only be used after confirm_order has successfully
    changed the order status to confirmed. The application will reject
    submission from any other state.
    """,
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    }
},
    {
    "type": "function",
    "name": "start_new_order",
    "description": """
    Start a fresh order after the customer's previous order
    has already been confirmed or submitted.
    """,
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False
    }
    },
    
]


# --------------------------------------------------
# TOOL EXECUTION
# --------------------------------------------------

def execute_tool(name, arguments, turn_id=None):
    print(f"[TOOL CALL] {name} {arguments}")


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
    
    if name == "modify_order_item":
        return modify_order_item(
        arguments["search_term"],
        arguments["ingredient"]
    )
        
    if name == "customize_order_item":
        return customize_order_item(
        arguments["search_term"],
        arguments["ingredient"],
        arguments["quantity"]
    )

    if name == "customize_order_line":
        return customize_order_line(
            arguments["line_id"],
            arguments["ingredient"],
            arguments["quantity"]
        )
    if name == "validate_order":
        return validate_order(turn_id)

    if name == "confirm_order":
        return confirm_order(turn_id)
    
    if name == "submit_order":
        return submit_order()
    
    if name == "start_new_order":
     return start_new_order()
    
    return {
        "error": f"Unknown tool: {name}"
    
    }


# --------------------------------------------------
# AGENT
# --------------------------------------------------



def run_agent(user_message,previous_response_id=None,turn_id=None):
    


    request = {
        "model": "gpt-5.4-mini",
        "instructions": """
You are the AI receptionist for OrderAI Burger House.

ROLE
Help customers browse the menu, build an order, review it, and complete it through natural conversation.

GENERAL BEHAVIOUR
- Speak naturally and keep responses concise.
- Do not invent menu items, prices, order contents, totals, or tool results.
- Use tools whenever the customer's request requires reading or changing order state.
- Do not claim an action succeeded until the relevant tool confirms success.
- If a tool returns an error, ambiguity, or failure, explain it briefly and ask for the minimum clarification needed.

ORDER ACTIONS
- When the customer wants to add an item, use add_item_to_order.
- When the customer asks what is currently in their order, use get_order.
- Never answer questions about the current basket from memory when get_order can provide the actual state.
- Never claim an item was added unless add_item_to_order succeeds.

TOOL USAGE
- Treat tool results as the source of truth for order state.
- Do not guess tool arguments when the customer's request is ambiguous.
- If the customer gives enough information, call the tool directly rather than asking unnecessary follow-up questions.
- After a successful tool call, respond naturally using the result.
- Do not expose internal tool names or implementation details to the customer.

CONVERSATION STYLE
- Act like a restaurant receptionist, not a technical assistant.
- Prefer short spoken responses.
- Avoid repeating the full order unless the customer asks for a summary or confirmation.
- Ask one clarification question at a time.

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
                arguments,
                turn_id=turn_id)

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
            
            After the customer explicitly confirms an order that is awaiting
            confirmation:

            1. Use confirm_order.
            2. If confirm_order succeeds, immediately use submit_order.
            3. Only tell the customer their order has been placed after
            submit_order returns "submitted".
            
            When the customer asks what is currently in their order,
            use get_order.

            Never invent order contents or totals.
            Use get_order to check the actual basket.
            
            When the customer asks about menu items, prices,
            available burgers, drinks, sides, or whether an item is sold,
            use search_menu.

            Do not invent menu items or prices.
            Use search_menu as the source of truth for menu questions.
            """,
            previous_response_id=response.id,
            input=tool_outputs,
            tools=tools
        )