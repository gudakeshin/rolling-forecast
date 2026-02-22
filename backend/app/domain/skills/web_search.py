"""web_search skill — Perplexity Sonar API for grounded web search with citations."""

import logging
from typing import Any

from app.config import settings
from app.domain.base_skill import BaseSkill, SkillContext, SkillResult

logger = logging.getLogger(__name__)


class WebSearchSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for current information using Perplexity Sonar. "
            "Returns synthesized answers with citations. Use for market news, "
            "competitor analysis, regulatory updates, and current data."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query for web search",
                },
                "search_focus": {
                    "type": "string",
                    "description": "Focus area: internet (general), finance, or news",
                    "default": "internet",
                },
                "detail_level": {
                    "type": "string",
                    "description": "Level of detail: quick (sonar) or detailed (sonar-pro)",
                    "default": "detailed",
                },
            },
            "required": ["query"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        query = params.get("query", "")
        if not query:
            return SkillResult.fail("A search query is required.")

        if not settings.perplexity_api_key:
            return SkillResult.fail(
                "Perplexity API key is not configured. "
                "Please set PERPLEXITY_API_KEY in your .env file."
            )

        search_focus = params.get("search_focus", "internet")
        detail_level = params.get("detail_level", "detailed")
        model = "sonar-pro" if detail_level == "detailed" else "sonar"

        system_prompt = self._build_system_prompt(search_focus)

        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=settings.perplexity_api_key,
                base_url="https://api.perplexity.ai",
            )

            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
            )

            answer = response.choices[0].message.content or ""
            citations = []
            if hasattr(response, "citations") and response.citations:
                citations = response.citations
            elif hasattr(response.choices[0].message, "context") and response.choices[0].message.context:
                ctx = response.choices[0].message.context
                if isinstance(ctx, dict) and "citations" in ctx:
                    citations = ctx["citations"]

            content_blocks = [self._text_block(answer)]

            if citations:
                cite_lines = ["\n**Sources:**"]
                for i, url in enumerate(citations[:10], 1):
                    if isinstance(url, dict):
                        cite_lines.append(f"{i}. [{url.get('title', url.get('url', ''))}]({url.get('url', '')})")
                    else:
                        cite_lines.append(f"{i}. {url}")
                content_blocks.append(self._text_block("\n".join(cite_lines)))

            return SkillResult.ok(
                message=f"Web search completed for: {query}",
                data={
                    "answer": answer,
                    "citations": citations,
                    "model": model,
                    "query": query,
                },
                content_blocks=content_blocks,
            )

        except Exception as e:
            logger.error(f"Perplexity API error: {e}", exc_info=True)
            return SkillResult.fail(
                f"Web search failed: {str(e)}",
                error=str(e),
            )

    def _build_system_prompt(self, focus: str) -> str:
        base = "You are a research assistant for a financial planning and analysis team."
        if focus == "finance":
            return (
                f"{base} Focus on financial markets, company earnings, economic indicators, "
                "and investment-relevant information. Provide specific numbers and dates."
            )
        elif focus == "news":
            return (
                f"{base} Focus on recent news, industry developments, and breaking stories. "
                "Prioritize the most recent and reliable sources."
            )
        return (
            f"{base} Provide concise, well-sourced answers. "
            "Include specific data points, dates, and numbers when available."
        )
