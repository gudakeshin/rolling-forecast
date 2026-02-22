"""fetch_url skill — fetches a URL and optionally indexes it in the context engine."""

import logging
from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.services import context_engine, document_processor

logger = logging.getLogger(__name__)


class FetchURLSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "fetch_url"

    @property
    def description(self) -> str:
        return (
            "Fetch content from a URL and optionally save it to the context engine "
            "for future reference. Extracts readable text from web pages."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch and read",
                },
                "save_to_context": {
                    "type": "boolean",
                    "description": "Whether to index the content for future searches",
                    "default": True,
                },
                "scope": {
                    "type": "string",
                    "description": "Scope for saved content: conversation or user",
                    "default": "conversation",
                },
            },
            "required": ["url"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        url = params.get("url", "").strip()
        if not url:
            return SkillResult.fail("A URL is required.")

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        save_to_context = params.get("save_to_context", True)
        scope = params.get("scope", "conversation")

        try:
            pages = document_processor.load_url(url)
        except Exception as e:
            logger.error(f"Failed to fetch URL {url}: {e}")
            return SkillResult.fail(f"Could not fetch URL: {str(e)}")

        if not pages:
            return SkillResult.fail("No readable content could be extracted from the URL.")

        full_text = "\n\n".join(p["content"] for p in pages)
        title = pages[0].get("metadata", {}).get("title", url)

        preview = full_text[:2000]
        if len(full_text) > 2000:
            preview += f"\n\n... ({len(full_text) - 2000} more characters)"

        content_blocks = [
            self._text_block(f"**{title}**\n\n{preview}")
        ]

        if save_to_context:
            try:
                doc = await context_engine.ingest_url(
                    db=context.db,
                    url=url,
                    user_id=context.user_id,
                    scope=scope,
                    conversation_id=context.conversation_id,
                    tags=["web"],
                )
                content_blocks.append(
                    self._text_block(
                        f"\n*Indexed {doc.chunk_count} chunks from this page for future reference.*"
                    )
                )
            except Exception as e:
                logger.warning(f"Failed to index URL: {e}")
                content_blocks.append(
                    self._text_block("\n*Note: Content was read but could not be indexed.*")
                )

        return SkillResult.ok(
            message=f"Fetched content from {url} ({len(full_text)} characters)",
            data={
                "url": url,
                "title": title,
                "char_count": len(full_text),
                "saved": save_to_context,
            },
            content_blocks=content_blocks,
        )
