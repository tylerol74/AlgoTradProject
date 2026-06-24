"""Readable SEC XBRL concept mappings and precedence rules."""

from typing import Dict, List, Optional

SUPPORTED_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
EXPECTED_UNITS = {
    "diluted_eps": "USD/shares",
    "basic_eps": "USD/shares",
    "weighted_average_diluted_shares": "shares",
    "weighted_average_basic_shares": "shares",
    "shares_outstanding": "shares",
    "total_debt": "USD",
    "debt_current": "USD",
    "debt_noncurrent": "USD",
    "short_term_borrowings": "USD",
    "commercial_paper": "USD",
    "current_portion_of_long_term_debt": "USD",
    "long_term_debt_current": "USD",
    "long_term_debt_noncurrent": "USD",
    "long_term_debt": "USD",
    "finance_lease_liabilities_current": "USD",
    "finance_lease_liabilities_noncurrent": "USD",
    "capital_lease_obligations": "USD",
    "notes_payable": "USD",
}

CONCEPT_MAPPINGS: Dict[str, List[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "diluted_eps": ["EarningsPerShareDiluted"],
    "basic_eps": ["EarningsPerShareBasic"],
    "interest_expense": ["InterestExpenseNonOperating", "InterestExpense"],
    "total_assets": ["Assets"],
    "total_liabilities": ["Liabilities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "inventory": ["InventoryNet"],
    "accounts_receivable": ["AccountsReceivableNetCurrent"],
    "short_term_borrowings": ["ShortTermBorrowings", "ShortTermBankLoansAndNotesPayable", "ShortTermDebt"],
    "commercial_paper": ["CommercialPaper"],
    "current_portion_of_long_term_debt": ["CurrentPortionOfLongTermDebt"],
    "long_term_debt_current": ["LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent"],
    "long_term_debt_noncurrent": ["LongTermDebtAndFinanceLeaseObligationsNoncurrent"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "debt_current": ["DebtCurrent"],
    "debt_noncurrent": ["DebtNoncurrent"],
    "finance_lease_liabilities_current": ["FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilitiesCurrent"],
    "finance_lease_liabilities_noncurrent": ["FinanceLeaseLiabilityNoncurrent", "FinanceLeaseLiabilitiesNoncurrent"],
    "capital_lease_obligations": ["CapitalLeaseObligations", "CapitalLeaseObligationsCurrent", "CapitalLeaseObligationsNoncurrent"],
    "notes_payable": ["NotesPayable", "NotesPayableCurrent", "NotesPayableToBank"],
    "total_debt": [
        "DebtCurrentAndNoncurrent",
        "LongTermDebtAndFinanceLeaseObligationsCurrentAndNoncurrent",
        "ShortTermBorrowingsAndLongTermDebt",
        "ShortTermBorrowingsAndLongTermDebtIncludingCurrentMaturities",
    ],
    "shareholders_equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capital_expenditures": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "dividends_paid": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "weighted_average_diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "weighted_average_basic_shares": ["WeightedAverageNumberOfSharesOutstandingBasic"],
    "shares_outstanding": ["EntityCommonStockSharesOutstanding", "CommonStocksIncludingAdditionalPaidInCapitalMember"],
}

CONCEPT_TO_FIELD: Dict[str, str] = {
    concept: field
    for field, concepts in CONCEPT_MAPPINGS.items()
    for concept in concepts
}


def standardized_field_for_concept(concept: str) -> Optional[str]:
    """Return the internal field for a SEC concept, if supported."""
    return CONCEPT_TO_FIELD.get(concept)


def concept_precedence(standardized_field: str, concept: str) -> int:
    """Return deterministic precedence for equivalent concepts."""
    concepts = CONCEPT_MAPPINGS.get(standardized_field, [])
    return concepts.index(concept) if concept in concepts else len(concepts)
