"""Adapters. Each implements `contracts.SourceAdapter`; none imports another."""
from .feed import FeedAdapter
from .page import PageAdapter, build_adapter

__all__ = ["FeedAdapter", "PageAdapter", "build_adapter"]
