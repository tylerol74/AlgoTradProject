"""Typed models for the Graham value strategy."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class EPSMethod(str, Enum):
    """EPS source method."""

    TTM_DILUTED = "TTM_DILUTED"
    ANNUAL_DILUTED = "ANNUAL_DILUTED"
    TTM_BASIC = "TTM_BASIC"
    ANNUAL_BASIC = "ANNUAL_BASIC"
    UNAVAILABLE = "UNAVAILABLE"


class DataQualityClass(str, Enum):
    """Data-quality classifications."""

    HIGH_CONFIDENCE = "High confidence"
    GOOD = "Good"
    USABLE_WITH_WARNINGS = "Usable with warnings"
    LOW_CONFIDENCE = "Low confidence"
    INSUFFICIENT = "Insufficient"


class QualificationStatus(str, Enum):
    """Final qualification state."""

    QUALIFIED = "QUALIFIED"
    FAILED = "FAILED"
    RESEARCH_ONLY = "RESEARCH_ONLY"


class GrahamClassification(str, Enum):
    """Graham quality classification."""

    EXCEPTIONAL = "Exceptional Graham candidate"
    STRONG = "Strong candidate"
    QUALIFIED = "Qualified candidate"
    WEAK = "Weak qualified candidate"
    NOT_QUALIFIED = "Not qualified"


class GrahamSignalType(str, Enum):
    """Graham signal type."""

    NONE = "NONE"
    GRAHAM_CANDIDATE = "GRAHAM_CANDIDATE"
    STRONG_GRAHAM_CANDIDATE = "STRONG_GRAHAM_CANDIDATE"


@dataclass(frozen=True)
class GrahamInputs:
    """Point-in-time inputs used by Graham calculations."""

    ticker: str
    evaluation_date: str
    market_price: Optional[float]
    average_dollar_volume_20d: Optional[float]
    shares_outstanding: Optional[float]
    market_cap: Optional[float]
    eps: Optional[float]
    eps_method: EPSMethod
    net_income: Optional[float]
    current_assets: Optional[float]
    current_liabilities: Optional[float]
    total_assets: Optional[float]
    total_liabilities: Optional[float]
    long_term_debt: Optional[float]
    total_debt: Optional[float]
    shareholders_equity: Optional[float]
    preferred_equity: Optional[float]
    goodwill: Optional[float]
    intangible_assets: Optional[float]
    operating_income: Optional[float]
    interest_expense: Optional[float]
    operating_cash_flow: Optional[float]
    filing_metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GrahamMetrics:
    """Derived Graham metrics and scores."""

    book_value_per_share: Optional[float]
    tangible_book_value_per_share: Optional[float]
    graham_number: Optional[float]
    tangible_graham_number: Optional[float]
    margin_of_safety: Optional[float]
    tangible_margin_of_safety: Optional[float]
    price_to_earnings: Optional[float]
    price_to_book: Optional[float]
    pe_times_pb: Optional[float]
    current_ratio: Optional[float]
    net_current_assets: Optional[float]
    debt_to_equity: Optional[float]
    interest_coverage: Optional[float]
    positive_earnings_years: int
    total_earnings_years: int
    five_year_eps_growth: Optional[float]
    earnings_volatility: Optional[float]
    data_quality_score: float
    graham_quality_score: float
    category_scores: Dict[str, Any] = field(default_factory=dict)
    data_quality_classification: DataQualityClass = DataQualityClass.INSUFFICIENT


@dataclass(frozen=True)
class GrahamEvaluation:
    """Complete Graham evaluation for one ticker/date."""

    ticker: str
    evaluation_date: str
    inputs: GrahamInputs
    metrics: GrahamMetrics
    eligibility_status: QualificationStatus
    qualification_status: QualificationStatus
    classification: GrahamClassification
    signal_action: str
    signal_type: GrahamSignalType
    disqualification_reasons: List[str]
    warnings: List[str]
    source_metadata: Dict[str, Any]
