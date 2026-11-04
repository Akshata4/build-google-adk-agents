"""Offline tests for the citation helpers (no Gemini calls)."""

from types import SimpleNamespace

from google.adk.models import LlmResponse
from google.genai import types

from lead_generation_agent.sub_agents.common import (
    resolve_sources,
    save_grounding_sources,
)


def _response(text: str, supports: list[tuple[str, list[int]]]) -> LlmResponse:
    """Build a grounded response; each support marks the end of a substring."""
    chunks = [
        types.GroundingChunk(
            web=types.GroundingChunkWeb(uri=f"https://s{i}", title=f"site{i}.com")
        )
        for i in range(3)
    ]
    grounding_supports = []
    for fragment, indices in supports:
        end = text.encode().index(fragment.encode()) + len(fragment.encode())
        grounding_supports.append(
            types.GroundingSupport(
                segment=types.Segment(end_index=end), grounding_chunk_indices=indices
            )
        )
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        grounding_metadata=types.GroundingMetadata(
            grounding_chunks=chunks, grounding_supports=grounding_supports
        ),
    )


def test_markers_inserted_after_each_supported_segment():
    ctx = SimpleNamespace(state={})
    text = "Hired a country manager in 2021. Registered a Thai company — ATOME."
    response = save_grounding_sources("sources_0")(
        ctx,
        _response(text, [("in 2021.", [0]), ("ATOME.", [2, 1])]),
    )
    assert response.content.parts[0].text == (
        "Hired a country manager in 2021.[1] Registered a Thai company — ATOME.[2][3]"
    )
    assert [s["url"] for s in ctx.state["sources_0"]] == [
        "https://s0",
        "https://s1",
        "https://s2",
    ]


def test_sources_accumulate_without_duplicates_across_calls():
    ctx = SimpleNamespace(
        state={"sources_0": [{"title": "site2.com", "url": "https://s2"}]}
    )
    response = save_grounding_sources("sources_0")(
        ctx, _response("A fact.", [("A fact.", [2])])
    )
    # s2 was already source [1], so the marker reuses that number.
    assert response.content.parts[0].text == "A fact.[1]"
    assert len(ctx.state["sources_0"]) == 3


def test_ungrounded_response_is_left_alone():
    ctx = SimpleNamespace(state={})
    response = LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text="hi")])
    )
    assert save_grounding_sources("sources_0")(ctx, response) is None
    assert "sources_0" not in ctx.state


def test_resolve_sources_maps_numbers_and_rejects_out_of_range():
    sources = [{"title": "a.com", "url": "https://a"}]
    finding = {
        "signals": [{"source_number": 1}, {"source_number": 0}, {"source_number": 7}]
    }
    resolved = resolve_sources(finding, sources)["signals"]
    assert resolved[0]["source"] == sources[0]
    assert resolved[1]["source"] is None
    assert resolved[2]["source"] is None
    assert all("source_number" not in s for s in resolved)
