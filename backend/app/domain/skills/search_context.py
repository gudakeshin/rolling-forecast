"""search_context skill — RAG over uploaded documents in the context engine."""

from typing import Any

from app.domain.base_skill import BaseSkill, SkillContext, SkillResult
from app.services import context_engine


class SearchContextSkill(BaseSkill):
    @property
    def name(self) -> str:
        return "search_context"

    @property
    def description(self) -> str:
        return (
            "Search uploaded documents and indexed content using semantic search. "
            "Returns the most relevant passages from the user's document library."
        )

    @property
    def parameters_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant content in uploaded documents",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return",
                    "default": 5,
                },
                "file_type": {
                    "type": "string",
                    "description": "Filter by file type (pdf, docx, xlsx, csv, url, etc.)",
                },
                "scope": {
                    "type": "string",
                    "description": "Filter by scope (user, conversation, global)",
                },
            },
            "required": ["query"],
        }

    async def execute(self, params: dict[str, Any], context: SkillContext) -> SkillResult:
        query = params.get("query", "")
        if not query:
            return SkillResult.fail("A search query is required.")

        top_k = params.get("top_k", 5)
        filters: dict[str, Any] = {}
        if params.get("file_type"):
            filters["file_type"] = params["file_type"]
        if params.get("scope"):
            filters["scope"] = params["scope"]

        results = context_engine.search_documents(
            db=context.db,
            query=query,
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            top_k=top_k,
            filters=filters,
        )

        if not results:
            return SkillResult.ok(
                message="No relevant documents found for your query. Try uploading some documents first.",
                data={"results": [], "query": query},
            )

        content_blocks = []
        summary_lines = [f"Found **{len(results)} relevant passages** for: *{query}*\n"]

        for i, r in enumerate(results, 1):
            source = r["original_name"] or "Unknown"
            score_pct = int(r["score"] * 100)
            snippet = r["content"][:400].strip()
            if len(r["content"]) > 400:
                snippet += "..."

            summary_lines.append(
                f"**[{i}] {source}** (relevance: {score_pct}%)\n{snippet}\n"
            )

        content_blocks.append(self._text_block("\n".join(summary_lines)))
        citations = [
            {
                "id": r.get("chunk_id") or str(i),
                "label": r.get("original_name") or "Document",
                "document_id": r.get("document_id"),
                "snippet": (r.get("content") or "")[:240],
                "score": r.get("score"),
            }
            for i, r in enumerate(results, 1)
        ]
        content_blocks.append(self._citations_block(citations))
        content_blocks.append(
            self._panel_trigger(
                "document_library",
                {"conversation_id": context.conversation_id},
                "Open Document Library",
            )
        )

        return SkillResult.ok(
            message=f"Found {len(results)} relevant passages across your documents.",
            data={"results": results, "query": query, "citations": citations},
            content_blocks=content_blocks,
        )
