"""
Juniper AI Tools

Service tools for invoking Juniper's AI capabilities within automations.
Enables AI-powered summarization, analysis, content generation, and web search.
"""

import json
import logging
import time
from typing import Dict, Any, List
from uuid import UUID
from app.config import LLM_CLASSIFIER_MODEL, THIRD_PARTY_SERVICE_TIMEOUT


from supabase import Client as SupabaseClient

from app.agents.models import Message, Action
from app.agents.integrations.service_tools.base_service import (
    create_service_action,
    parse_tool_input
)

logger = logging.getLogger(__name__)


def _get_provider_for_model(model: str, retry_config):
    """Select the appropriate provider based on model name."""
    from app.agents.model_providers import (
        AnthropicProvider, GoogleProvider, OpenAIProvider, GrokProvider
    )

    model_lower = model.lower()

    if model_lower.startswith('gemini'):
        return GoogleProvider(model=model, retry_config=retry_config)
    elif model_lower.startswith(('gpt', 'o3', 'o1')):
        return OpenAIProvider(model=model, retry_config=retry_config)
    elif model_lower.startswith('grok'):
        return GrokProvider(model=model, retry_config=retry_config)
    else:
        # Default to Anthropic for claude-*, haiku-*, sonnet-*, opus-*, etc.
        return AnthropicProvider(model=model, retry_config=retry_config)


async def juniper_llm_classify(user_id: UUID, params: Dict[str, Any], supabase: SupabaseClient) -> str:
    """
    Lightweight LLM classification.

    Use for simple yes/no or category decisions within automations.
    Much faster and cheaper than calling the full agent.

    Args:
        user_id: User ID for context
        params: Dict with:
            - text (required): The text to classify
            - question (required): The classification question
            - options (optional): List of valid answers, defaults to ["YES", "NO"]
        supabase: Supabase client

    Returns:
        JSON string with {answer: str, reasoning: str}
    """
    from app.agents.model_providers import RetryConfig

    text = params.get("text", "")
    question = params.get("question", "")
    options = params.get("options", ["YES", "NO"])

    if not text:
        return json.dumps({"error": "text parameter is required"})
    if not question:
        return json.dumps({"error": "question parameter is required"})

    # Build the classification prompt
    options_str = ", ".join(options)
    prompt = f"""You are a precise classifier.

Question: {question}

Text to analyze:
"{text}"

Valid answers: {options_str}

Respond with ONLY a JSON object in this exact format:
{{"answer": "<one of: {options_str}>", "reasoning": "<brief 1-sentence explanation>"}}"""

    try:
        retry_config = RetryConfig(max_retries=2, base_delay=0.5, enable_fallback=False)
        provider = _get_provider_for_model(LLM_CLASSIFIER_MODEL, retry_config)

        messages = [
            {"role": "user", "content": prompt, "type": "text"}
        ]

        response = provider.generate_response(messages, temperature=0.0)

        # Parse and validate response
        cleaned = response.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        result = json.loads(cleaned)

        # Validate answer is one of the options
        if result.get("answer") not in options:
            # Try to match case-insensitively
            answer_lower = result.get("answer", "").upper()
            for opt in options:
                if opt.upper() == answer_lower:
                    result["answer"] = opt
                    break

        logger.info(f"LLM classify for user {user_id}: {result.get('answer')}")
        return json.dumps(result)

    except json.JSONDecodeError as e:
        logger.warning(f"LLM classify parse error: {e}, raw: {response[:200]}")
        return json.dumps({"answer": options[0], "reasoning": "Parse error, defaulting", "error": str(e)})
    except Exception as e:
        logger.exception(f"LLM classify error for user {user_id}")
        return json.dumps({"error": str(e)})


async def juniper_llm_classify_handler(input_str: str, user_id: str, supabase: SupabaseClient) -> str:
    """Handler wrapper for juniper_llm_classify tool."""
    params = parse_tool_input(input_str)
    actual_user_id = params.pop('user_id', None) or user_id

    if not actual_user_id:
        return json.dumps({"error": "user_id is required"})

    try:
        if isinstance(actual_user_id, UUID):
            user_uuid = actual_user_id
        else:
            user_uuid = UUID(str(actual_user_id).strip())
    except (ValueError, AttributeError):
        return json.dumps({"error": "Invalid user_id format"})

    return await juniper_llm_classify(user_uuid, params, supabase)


async def juniper_llm_transform(user_id: UUID, params: Dict[str, Any], supabase: SupabaseClient) -> str:
    """
    Lightweight LLM transformation for formatting and restructuring.

    Use for tasks like JSON conversion, sorting, text formatting, data restructuring.
    Much faster and cheaper than calling the full agent.

    Args:
        user_id: User ID for context
        params: Dict with:
            - text (required): The text to transform
            - instruction (required): The transformation instruction
            - output_format (optional): 'text' or 'json' (default: 'text')
        supabase: Supabase client

    Returns:
        Transformed text or JSON string
    """
    from app.agents.model_providers import RetryConfig
    from app.config import LLM_TRANSFORM_MODEL, LLM_TRANSFORM_TEMPERATURE

    # Accept multiple parameter name variants for flexibility
    text = params.get("text") or params.get("input") or params.get("input_text", "")
    instruction = params.get("instruction") or params.get("transformation_instruction", "")
    output_format = params.get("output_format", "text")

    if not text:
        return json.dumps({"error": "text parameter is required (also accepts 'input' or 'input_text')"})
    if not instruction:
        return json.dumps({"error": "instruction parameter is required (also accepts 'transformation_instruction')"})

    # Build the transformation prompt
    if output_format == "json":
        prompt = f"""You are a precise text transformer.

Instruction: {instruction}

Text to transform:
"{text}"

Respond with ONLY valid JSON. No explanations, no markdown formatting, just the JSON output."""
    else:
        prompt = f"""You are a precise text transformer.

Instruction: {instruction}

Text to transform:
"{text}"

Respond with ONLY the transformed output. No explanations, no commentary, just the result."""

    try:
        retry_config = RetryConfig(max_retries=2, base_delay=0.5, enable_fallback=False)
        provider = _get_provider_for_model(LLM_TRANSFORM_MODEL, retry_config)

        messages = [
            {"role": "user", "content": prompt, "type": "text"}
        ]

        response = provider.generate_response(messages, temperature=LLM_TRANSFORM_TEMPERATURE)

        # Clean up response
        cleaned = response.strip()

        # If expecting JSON, try to parse and validate
        if output_format == "json":
            # Remove markdown code blocks if present
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            # Validate JSON
            try:
                json.loads(cleaned)
            except json.JSONDecodeError as e:
                logger.warning(f"LLM transform JSON parse error: {e}")
                return json.dumps({"error": f"Invalid JSON output: {str(e)}", "raw_output": cleaned[:200]})

        logger.info(f"LLM transform for user {user_id}: {len(cleaned)} chars output")
        return cleaned

    except Exception as e:
        logger.exception(f"LLM transform error for user {user_id}")
        return json.dumps({"error": str(e)})


async def juniper_llm_transform_handler(input_str: str, user_id: str, supabase: SupabaseClient) -> str:
    """Handler wrapper for juniper_llm_transform tool."""
    params = parse_tool_input(input_str)
    actual_user_id = params.pop('user_id', None) or user_id

    if not actual_user_id:
        return json.dumps({"error": "user_id is required"})

    try:
        if isinstance(actual_user_id, UUID):
            user_uuid = actual_user_id
        else:
            user_uuid = UUID(str(actual_user_id).strip())
    except (ValueError, AttributeError):
        return json.dumps({"error": "Invalid user_id format"})

    return await juniper_llm_transform(user_uuid, params, supabase)


def juniper_search_web(params: Dict[str, Any]) -> str:
    """
    Lightweight web search using Tavily API with Brave fallback.

    Use for simple factual lookups within automations.
    Much faster and cheaper than juniper_call_agent.
    No usage tracking or billing - completely lightweight.

    Tavily returns actual page content (not just snippets), making it
    reliable for extracting real-time data like stock prices.

    Args:
        params: Dict with:
            - query (required): The search query
            - max_results (optional): Max results to return (default 5, max 10)
            - topic (optional): Search category - 'general', 'news', or 'finance'
            - include_content (optional): Include full page content (default True)

    Returns:
        JSON string with search results including content
    """
    import requests
    import os

    query = params.get("query", "")
    max_results = min(params.get("max_results", 5), 10)
    topic = params.get("topic", "general")
    include_content = params.get("include_content", True)

    if not query:
        return json.dumps({"error": "query parameter is required"})

    # Try Tavily Search first (returns actual page content)
    result = _try_tavily_search(query, max_results, topic, include_content)
    if result:
        return json.dumps({"success": True, "source": "tavily", "results": result})

    # Fallback to Brave Search (snippets only)
    result = _try_brave_search_lite(query, max_results)
    if result:
        return json.dumps({"success": True, "source": "brave", "results": result})

    # Fallback to alternative search (Bing/Yahoo)
    result = _try_alternative_search_lite(query, max_results)
    if result:
        return json.dumps({"success": True, "source": "alternative", "results": result})

    return json.dumps({"success": False, "results": [], "message": "All search methods failed"})


def _try_tavily_search(query: str, max_results: int = 5, topic: str = "general", include_content: bool = True) -> List[Dict[str, Any]]:
    """
    Tavily Search - returns actual page content, not just snippets.

    This is superior to Brave for extracting real-time data like stock prices
    because it returns the actual page content where the data lives.
    """
    import requests
    import os

    tavily_api_key = os.getenv("TAVILY_API_KEY")
    if not tavily_api_key:
        logger.warning("Tavily API key not found, falling back to Brave")
        return None

    url = "https://api.tavily.com/search"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {tavily_api_key}"
    }

    payload = {
        "query": query,
        "max_results": max_results,
        "topic": topic,
        "include_raw_content": include_content,
        "search_depth": "basic"  # Use basic for cost efficiency (1 credit)
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=THIRD_PARTY_SERVICE_TIMEOUT)

        if response.status_code == 200:
            data = response.json()
            tavily_results = data.get("results", [])

            if not tavily_results:
                logger.debug("Tavily returned no results")
                return None

            results = []
            for r in tavily_results[:max_results]:
                result = {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),  # Main extracted content
                    "score": r.get("score", 0)
                }
                # Include raw_content if available and requested
                if include_content and r.get("raw_content"):
                    result["raw_content"] = r.get("raw_content", "")[:2000]  # Limit size
                results.append(result)

            logger.debug(f"Tavily search returned {len(results)} results for: {query[:50]}")
            return results

        elif response.status_code == 429:
            logger.warning("Tavily rate limit exceeded")
            return None
        else:
            logger.error(f"Tavily search returned {response.status_code}: {response.text[:200]}")
            return None

    except Exception as e:
        logger.error(f"Tavily search error: {str(e)}")
        return None


def _try_brave_search_lite(query: str, max_results: int = 5, max_retries: int = 2) -> List[Dict[str, str]]:
    """Lightweight Brave Search - returns structured results (snippets only)."""
    import requests
    import os

    brave_api_key = os.getenv("BRAVE_SEARCH_API_KEY")
    if not brave_api_key:
        logger.warning("Brave API key not found")
        return None

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": brave_api_key
    }
    params = {"q": query}

    delay = 0.5
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=THIRD_PARTY_SERVICE_TIMEOUT)

            if response.status_code == 200:
                data = response.json()
                web_results = data.get("web", {}).get("results", [])

                if not web_results:
                    return None

                results = []
                for r in web_results[:max_results]:
                    results.append({
                        "title": r.get("title", ""),
                        "snippet": r.get("description", ""),
                        "url": r.get("url", "")
                    })

                logger.debug(f"Brave search returned {len(results)} results for: {query[:50]}")
                return results

            elif response.status_code == 429 and attempt < max_retries:
                logger.warning(f"Brave rate limit, retrying in {delay}s")
                time.sleep(delay)
                delay *= 2
                continue
            else:
                logger.error(f"Brave search returned {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Brave search error (attempt {attempt}): {str(e)}")
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 1.5
                continue
            return None

    return None


def _try_alternative_search_lite(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Fallback search using Bing/Yahoo web scraping."""
    import requests
    import random
    import re
    from bs4 import BeautifulSoup

    search_engines = [
        {
            'name': 'Bing',
            'url': f"https://www.bing.com/search?q={query.replace(' ', '+')}",
            'result_selector': 'li.b_algo',
            'title_selector': 'h2',
            'snippet_selector': '.b_caption p',
            'link_selector': 'h2 a',
            'link_attribute': 'href'
        },
        {
            'name': 'Yahoo',
            'url': f"https://search.yahoo.com/search?p={query.replace(' ', '+')}",
            'result_selector': 'div.algo',
            'title_selector': 'h3',
            'snippet_selector': 'div.compText',
            'link_selector': 'h3 a',
            'link_attribute': 'href'
        }
    ]

    search_engine = random.choice(search_engines)
    logger.info(f"Using {search_engine['name']} as fallback search")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    }

    try:
        response = requests.get(search_engine['url'], headers=headers, timeout=THIRD_PARTY_SERVICE_TIMEOUT)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            html_results = soup.select(search_engine['result_selector'])

            if not html_results:
                logger.warning(f"No results from {search_engine['name']}")
                return None

            results = []
            for r in html_results[:max_results]:
                try:
                    title_elem = r.select_one(search_engine['title_selector'])
                    title = title_elem.get_text().strip() if title_elem else "No title"

                    snippet_elem = r.select_one(search_engine['snippet_selector'])
                    snippet = snippet_elem.get_text().strip() if snippet_elem else "No description"

                    link_elem = r.select_one(search_engine['link_selector'])
                    link = link_elem.get(search_engine['link_attribute']) if link_elem else "#"

                    # Clean Yahoo redirect URLs
                    if search_engine['name'] == 'Yahoo' and 'RU=' in link:
                        match = re.search(r'RU=([^/]*)', link)
                        if match:
                            link = requests.utils.unquote(match.group(1))

                    results.append({
                        "title": title,
                        "snippet": snippet,
                        "url": link
                    })
                except Exception as e:
                    logger.error(f"Error parsing result: {str(e)}")
                    continue

            if results:
                logger.debug(f"Alternative search returned {len(results)} results")
                return results

        logger.error(f"Alternative search returned status {response.status_code}")
        return None

    except Exception as e:
        logger.error(f"Alternative search error: {str(e)}")
        return None


def juniper_search_web_handler(input_str: str, user_id: str, supabase: SupabaseClient) -> str:
    """Handler wrapper for juniper_search_web tool."""
    params = parse_tool_input(input_str)
    # Note: user_id and supabase are accepted for interface consistency but not used
    # This tool is completely lightweight with no tracking
    return juniper_search_web(params)


async def juniper_call_agent(user_id: UUID, params: Dict[str, Any], supabase: SupabaseClient) -> str:
    """
    Call Juniper's AI chat for tasks within automations.

    Use cases:
    - Summarization (emails, messages, health data)
    - Analysis and recommendations
    - Content generation (messages, reminders)
    - Decision-making based on context

    Args:
        user_id: User ID for context
        params: Dict with 'message' (required) and 'context' (optional)
        supabase: Supabase client

    Returns:
        AI response as string
    """
    from app.agents.lead_agent.lead_agent import get_chat_response
    from app.auth import check_user_limits
    from app.config import WEB_APP_URL
    from app.services.stripe_service import stripe_service

    message = params.get("message")
    context = params.get("context", {})
    is_automation = params.get("is_automation", False)

    if not message:
        return "Error: 'message' parameter is required"

    # Track overage info for post-call billing
    will_incur_overage = False
    overage_cost_cents = 0
    stripe_customer_id = None

    # Check monthly/overage limits before making API call
    try:
        profile_result = supabase.from_('user_profiles').select(
            'id, subscription_tier, requests_month, ubp_current, ubp_max, stripe_customer_id'
        ).eq('id', str(user_id)).execute()

        if profile_result.data:
            user_profile = profile_result.data[0]
            stripe_customer_id = user_profile.get('stripe_customer_id')
            limit_check = check_user_limits(user_profile)

            # Capture overage info for billing after successful call
            will_incur_overage = limit_check.get('will_incur_overage', False)
            overage_cost_cents = limit_check.get('overage_cost_cents', 0)

            if not limit_check['can_proceed']:
                error_type = limit_check.get('error_type', 'unknown')
                logger.warning(f"User {user_id} exceeded {error_type} limit in juniper_call_agent")

                # Return structured error for automations
                if is_automation:
                    return json.dumps({
                        "error": "USAGE_LIMIT_EXCEEDED",
                        "error_type": error_type,
                        "service": "juniper",
                        "message": f"Usage limit reached ({error_type}). Manage your account at {WEB_APP_URL}"
                    })

                # Return friendly message for non-automation calls
                if error_type == 'monthly_requests':
                    return f"The user has reached the limit for monthly requests. Please notify them and let them know that they can manage their account in the web app at {WEB_APP_URL}."
                elif error_type == 'request_overage_ubp_limit':
                    return f"The user has reached the limit for overage requests. Please notify them and let them know that they can manage their account in the web app at {WEB_APP_URL}."
                else:
                    return f"The user has reached their usage limit. Please notify them and let them know that they can manage their account in the web app at {WEB_APP_URL}."
    except Exception as limit_error:
        logger.error(f"Error checking limits for user {user_id}: {str(limit_error)}")
        # Continue with request if limit check fails (fail-open)

    # Build context message if provided
    context_str = ""
    if context:
        if isinstance(context, dict):
            context_str = f"\n\nContext data:\n{json.dumps(context, indent=2)}"
        elif isinstance(context, str):
            context_str = f"\n\nContext:\n{context}"

    full_message = message + context_str

    # Create minimal message history with required fields
    messages: List[Message] = [
        Message(role="user", content=full_message, type="text", timestamp=int(time.time()))
    ]

    try:
        logger.info(f"Juniper chat called by automation for user {user_id}")

        # Call the lead agent
        response, _, _ = await get_chat_response(
            messages=messages,
            user_id=user_id,
            supabase=supabase,
            request_id=f"automation_{user_id}_{hash(message) % 10000}",
            integration_in_progress=False
        )

        if not response:
            return "Error: No response received from Juniper"

        logger.info(f"Juniper chat completed, response length: {len(response)}")

        # Track usage after successful response
        try:
            # Increment requests_today
            supabase.rpc('increment_user_usage', {
                'user_id': str(user_id),
                'usage_type': 'requests_today'
            }).execute()
            logger.info(f"Incremented requests_today for user {user_id} (automation)")

            # If overage, charge the cost and send Stripe meter event
            if will_incur_overage and overage_cost_cents > 0:
                supabase.rpc('increment_user_usage_with_cost', {
                    'user_id': str(user_id),
                    'usage_type': 'requests_month',
                    'cost_cents': overage_cost_cents
                }).execute()
                logger.info(f"Charged ${overage_cost_cents/100:.2f} request overage for user {user_id} (automation)")

                # Send Stripe meter event
                if stripe_customer_id:
                    try:
                        await stripe_service.send_request_overage_event(
                            stripe_customer_id=stripe_customer_id,
                            user_id=str(user_id)
                        )
                    except Exception as stripe_error:
                        logger.error(f"Error sending Stripe meter event for user {user_id}: {str(stripe_error)}")
                else:
                    logger.warning(f"No Stripe customer ID for user {user_id} - skipping meter event")

        except Exception as usage_error:
            logger.error(f"Error tracking usage for user {user_id}: {str(usage_error)}")
            # Don't fail the request if usage tracking fails

        return response

    except Exception as e:
        logger.exception(f"Juniper chat error for user {user_id}")
        return f"Error: {str(e)}"


async def juniper_call_agent_handler(input_str: str, user_id: str, supabase: SupabaseClient) -> str:
    """Handler wrapper for juniper_call_agent tool."""
    params = parse_tool_input(input_str)

    # Use user_id from params if available (set by declarative executor)
    # Fall back to closure-captured user_id for direct agent calls
    actual_user_id = params.pop('user_id', None) or user_id

    if not actual_user_id:
        return "Error: user_id is required"

    # Convert to UUID, handling both string and UUID inputs
    try:
        if isinstance(actual_user_id, UUID):
            user_uuid = actual_user_id
        else:
            user_uuid = UUID(str(actual_user_id).strip())
    except (ValueError, AttributeError) as e:
        logger.error(f"Invalid user_id format: {actual_user_id!r} (type: {type(actual_user_id).__name__})")
        return "Error: Invalid user_id format"

    # Call juniper_call_agent with validated UUID
    return await juniper_call_agent(user_uuid, params, supabase)


def get_juniper_tools(user_id: str = None, supabase: SupabaseClient = None) -> List[Action]:
    """
    Get Juniper AI tools for automation use.

    Args:
        user_id: User ID for tool context
        supabase: Supabase client

    Returns:
        List of Juniper AI tools
    """
    tools = []

    tools.append(create_service_action(
        name="juniper_call_agent",
        description="Call Juniper AI for summarization, analysis, content generation, or recommendations. Use for tasks requiring AI reasoning within automations.",
        parameters={
            "message": {
                "type": "string",
                "description": "The prompt or request to send to Juniper AI. Be specific about what you want.",
                "required": True
            },
            "context": {
                "type": "object",
                "description": "Optional additional context data (e.g., emails to summarize, health data to analyze)",
                "required": False
            }
        },
        returns="AI-generated response as text",
        handler_func=lambda input_str: juniper_call_agent_handler(input_str, user_id, supabase)
    ))

    tools.append(create_service_action(
        name="juniper_llm_classify",
        description="Lightweight LLM classification using a lightweight LLM. Use for simple yes/no or category decisions. Much faster and cheaper than juniper_call_agent.",
        parameters={
            "text": {
                "type": "string",
                "description": "The text to classify",
                "required": True
            },
            "question": {
                "type": "string",
                "description": "The classification question (e.g., 'Is this message about client approval?')",
                "required": True
            },
            "options": {
                "type": "array",
                "description": "Valid answer options. Defaults to ['YES', 'NO']",
                "required": False
            }
        },
        returns="JSON with {answer: string, reasoning: string}",
        handler_func=lambda input_str: juniper_llm_classify_handler(input_str, user_id, supabase)
    ))

    tools.append(create_service_action(
        name="juniper_llm_transform",
        description="Lightweight LLM transformation for formatting, restructuring, sorting, and converting text. Use for JSON conversion, data formatting, text cleanup. Much faster and cheaper than juniper_call_agent.",
        parameters={
            "text": {
                "type": "string",
                "description": "The text to transform (aliases: 'input', 'input_text')",
                "required": True
            },
            "instruction": {
                "type": "string",
                "description": "The transformation instruction (alias: 'transformation_instruction')",
                "required": True
            },
            "output_format": {
                "type": "string",
                "description": "Expected output format: 'text' or 'json'. Defaults to 'text'",
                "required": False
            }
        },
        returns="Transformed text or JSON string",
        handler_func=lambda input_str: juniper_llm_transform_handler(input_str, user_id, supabase)
    ))

    tools.append(create_service_action(
        name="juniper_search_web",
        description="Lightweight web search using Tavily API (with Brave fallback). Returns actual page content, not just snippets - reliable for extracting real-time data like stock prices. Use for factual lookups, current information, or research within automations. Much faster and cheaper than juniper_call_agent. No usage tracking.",
        parameters={
            "query": {
                "type": "string",
                "description": "The search query (e.g., 'AMZN stock price today')",
                "required": True
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (default 5, max 10)",
                "required": False
            },
            "topic": {
                "type": "string",
                "description": "Search category: 'general' (default), 'news', or 'finance'",
                "required": False
            }
        },
        returns="JSON with search results including titles, URLs, and extracted page content",
        handler_func=lambda input_str: juniper_search_web_handler(input_str, user_id, supabase)
    ))

    return tools
