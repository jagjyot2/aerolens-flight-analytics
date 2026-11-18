"""Extensible parser registry with format auto-detection and explicit format override."""

from __future__ import annotations

from dataclasses import dataclass

from backend.parsers.ardupilot_csv import ArduPilotParser
from backend.parsers.base import BaseFlightParser, ParsedFlight, ParserError
from backend.parsers.mavlink_tlog import MAVLinkParser
from backend.parsers.px4_ulog import PX4ULogParser


@dataclass(frozen=True)
class ParserDescriptor:
    format_id: str
    display_name: str
    supported_extensions: tuple[str, ...]


class ParserRegistry:
    """Registry that routes flight logs to the appropriate BaseFlightParser."""

    def __init__(self) -> None:
        self._parsers: dict[str, BaseFlightParser] = {}
        self._order: list[str] = []

    def register(self, parser: BaseFlightParser) -> None:
        self._parsers[parser.format_id] = parser
        if parser.format_id not in self._order:
            self._order.append(parser.format_id)

    def get_parser(self, format_id: str) -> BaseFlightParser:
        parser = self._parsers.get(format_id)
        if not parser:
            raise ParserError(f"Unsupported parser format_id: '{format_id}'")
        return parser

    def list_formats(self) -> list[ParserDescriptor]:
        return [
            ParserDescriptor(
                format_id=self._parsers[fid].format_id,
                display_name=self._parsers[fid].display_name,
                supported_extensions=self._parsers[fid].supported_extensions,
            )
            for fid in self._order
        ]

    def detect_parser(self, filename: str, content: bytes) -> BaseFlightParser:
        for fid in self._order:
            parser = self._parsers[fid]
            if parser.can_parse(filename, content):
                return parser
        raise ParserError(
            f"Could not detect a supported flight log format for '{filename or 'upload'}'."
        )

    def parse_bytes(
        self, content: bytes, filename: str = "", format_hint: str | None = None
    ) -> ParsedFlight:
        if format_hint:
            parser = self.get_parser(format_hint)
            return parser.parse(content, filename=filename)

        # Check specific detectors first (PX4 & MAVLink before generic CSV fallback)
        parser = self.detect_parser(filename, content)
        return parser.parse(content, filename=filename)


def create_default_registry() -> ParserRegistry:
    registry = ParserRegistry()
    # Register specific binary/structured formats before general CSV fallback
    registry.register(PX4ULogParser())
    registry.register(MAVLinkParser())
    registry.register(ArduPilotParser())
    return registry


default_registry = create_default_registry()
