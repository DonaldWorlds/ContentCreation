"""Game-agnostic detection core (Phase 1).

Pipeline: fuse -> cluster -> score -> threshold -> window -> dedupe -> budget.
Pure functions over Event/Candidate. No media, no adapters, no DB. The core only
sees an already-filtered list[Event]; audio/special signals enter via Event.weight
and Event.confidence (set by adapters), keeping the core signal-agnostic.
"""
