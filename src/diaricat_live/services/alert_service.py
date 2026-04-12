"""Keyword-based alert scanner for live transcripts."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class KeywordRule:
    pattern: str
    sector: str
    urgency: int  # 1-10
    _compiled: re.Pattern | None = field(default=None, repr=False)

    @property
    def regex(self) -> re.Pattern:
        if self._compiled is None:
            self._compiled = re.compile(
                rf"\b{re.escape(self.pattern)}\b",
                re.IGNORECASE,
            )
        return self._compiled


@dataclass
class AlertMatch:
    keyword: str
    sector: str
    urgency: int
    context: str  # surrounding text where the keyword was found


# Default keyword set focused on Argentine financial/economic news
DEFAULT_KEYWORDS: list[dict] = [
    # Finanzas / Cambio
    {"pattern": "dólar", "sector": "finanzas", "urgency": 7},
    {"pattern": "dolar", "sector": "finanzas", "urgency": 7},
    {"pattern": "blue", "sector": "finanzas", "urgency": 6},
    {"pattern": "contado con liqui", "sector": "finanzas", "urgency": 7},
    {"pattern": "CCL", "sector": "finanzas", "urgency": 7},
    {"pattern": "MEP", "sector": "finanzas", "urgency": 6},
    {"pattern": "cepo", "sector": "finanzas", "urgency": 9},
    {"pattern": "devaluación", "sector": "finanzas", "urgency": 9},
    {"pattern": "tipo de cambio", "sector": "finanzas", "urgency": 8},
    {"pattern": "crawling peg", "sector": "finanzas", "urgency": 8},
    {"pattern": "banda cambiaria", "sector": "finanzas", "urgency": 8},
    # BCRA / Política monetaria
    {"pattern": "BCRA", "sector": "finanzas", "urgency": 8},
    {"pattern": "banco central", "sector": "finanzas", "urgency": 8},
    {"pattern": "tasa de interés", "sector": "finanzas", "urgency": 7},
    {"pattern": "BADLAR", "sector": "finanzas", "urgency": 6},
    {"pattern": "reservas", "sector": "finanzas", "urgency": 7},
    {"pattern": "encajes", "sector": "finanzas", "urgency": 6},
    # Economía
    {"pattern": "inflación", "sector": "economia", "urgency": 7},
    {"pattern": "IPC", "sector": "economia", "urgency": 7},
    {"pattern": "recesión", "sector": "economia", "urgency": 8},
    {"pattern": "PBI", "sector": "economia", "urgency": 7},
    {"pattern": "déficit", "sector": "economia", "urgency": 7},
    {"pattern": "superávit", "sector": "economia", "urgency": 7},
    {"pattern": "FMI", "sector": "economia", "urgency": 9},
    {"pattern": "desembolso", "sector": "economia", "urgency": 8},
    # Mercado
    {"pattern": "merval", "sector": "mercado", "urgency": 6},
    {"pattern": "bonos", "sector": "mercado", "urgency": 6},
    {"pattern": "riesgo país", "sector": "mercado", "urgency": 7},
    {"pattern": "ADR", "sector": "mercado", "urgency": 6},
    # Sindical / Social
    {"pattern": "paritarias", "sector": "sindical", "urgency": 6},
    {"pattern": "paro", "sector": "sindical", "urgency": 8},
    {"pattern": "huelga", "sector": "sindical", "urgency": 8},
    {"pattern": "CGT", "sector": "sindical", "urgency": 7},
    # Energía / Commodities
    {"pattern": "YPF", "sector": "energia", "urgency": 6},
    {"pattern": "Vaca Muerta", "sector": "energia", "urgency": 6},
    {"pattern": "tarifas", "sector": "energia", "urgency": 7},
    {"pattern": "petróleo", "sector": "energia", "urgency": 6},
    {"pattern": "soja", "sector": "agro", "urgency": 6},
]


class AlertService:
    """Scans transcript text for financial/economic keywords and emits alerts."""

    def __init__(self, extra_keywords_file: Path | None = None) -> None:
        self.rules: list[KeywordRule] = []
        self._load_defaults()
        if extra_keywords_file:
            self._load_from_yaml(extra_keywords_file)

    def add_keywords(self, keywords: list[str], sector: str = "custom", urgency: int = 7) -> None:
        """Add extra keywords at runtime (e.g. from a StartStreamRequest)."""
        for kw in keywords:
            if not any(r.pattern.lower() == kw.lower() for r in self.rules):
                self.rules.append(KeywordRule(pattern=kw, sector=sector, urgency=urgency))

    def scan(self, text: str) -> list[AlertMatch]:
        """Scan a transcript segment for keyword matches."""
        matches: list[AlertMatch] = []
        seen: set[str] = set()

        for rule in self.rules:
            if rule.regex.search(text) and rule.pattern.lower() not in seen:
                seen.add(rule.pattern.lower())
                matches.append(
                    AlertMatch(
                        keyword=rule.pattern,
                        sector=rule.sector,
                        urgency=rule.urgency,
                        context=self._extract_context(text, rule),
                    )
                )

        return matches

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_defaults(self) -> None:
        for kw in DEFAULT_KEYWORDS:
            self.rules.append(KeywordRule(**kw))

    def _load_from_yaml(self, path: Path) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for entry in data.get("keywords", []):
                self.rules.append(
                    KeywordRule(
                        pattern=entry["pattern"],
                        sector=entry.get("sector", "custom"),
                        urgency=entry.get("urgency", 7),
                    )
                )
            logger.info("Loaded %d extra keywords from %s", len(data.get("keywords", [])), path)
        except Exception:
            logger.exception("Failed to load keywords from %s", path)

    @staticmethod
    def _extract_context(text: str, rule: KeywordRule, window: int = 80) -> str:
        """Extract surrounding context around the keyword match."""
        match = rule.regex.search(text)
        if not match:
            return text[:160]

        start = max(0, match.start() - window)
        end = min(len(text), match.end() + window)
        ctx = text[start:end].strip()

        if start > 0:
            ctx = "..." + ctx
        if end < len(text):
            ctx = ctx + "..."

        return ctx
