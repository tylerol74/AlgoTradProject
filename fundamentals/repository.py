"""Repository functions for point-in-time fundamental filings and facts."""

from typing import Any, Dict, List, Optional, Sequence

from database.connection import get_connection


def upsert_filing(filing: Dict[str, Any], database_path=None) -> int:
    """Insert/update a filing and return its filing_id."""
    params = (
        filing["ticker"],
        filing["cik"],
        filing["accession_number"],
        filing["form_type"],
        filing["filing_date"],
        filing.get("accepted_at"),
        filing.get("report_period"),
        filing.get("fiscal_year"),
        filing.get("fiscal_period"),
        1 if filing.get("is_amendment") else 0,
        filing.get("source_url"),
        filing["downloaded_at"],
    )
    with get_connection(database_path) as connection:
        connection.execute(
            """
            INSERT INTO fundamental_filings (
                ticker, cik, accession_number, form_type, filing_date, accepted_at, report_period,
                fiscal_year, fiscal_period, is_amendment, source_url, downloaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cik, accession_number) DO UPDATE SET
                ticker = excluded.ticker,
                form_type = excluded.form_type,
                filing_date = excluded.filing_date,
                accepted_at = excluded.accepted_at,
                report_period = excluded.report_period,
                fiscal_year = excluded.fiscal_year,
                fiscal_period = excluded.fiscal_period,
                is_amendment = excluded.is_amendment,
                source_url = excluded.source_url,
                downloaded_at = excluded.downloaded_at
            """,
            params,
        )
        row = connection.execute(
            "SELECT filing_id FROM fundamental_filings WHERE cik = ? AND accession_number = ?",
            (filing["cik"], filing["accession_number"]),
        ).fetchone()
    return int(row["filing_id"])


def upsert_fundamental_facts(facts: Sequence[Dict[str, Any]], database_path=None) -> int:
    """Bulk insert/update fact rows."""
    if not facts:
        return 0
    params = [
        (
            fact["filing_id"],
            fact["ticker"],
            fact["taxonomy"],
            fact["concept"],
            fact.get("standardized_field"),
            fact.get("unit"),
            fact.get("value"),
            fact.get("period_start"),
            fact.get("period_end"),
            fact.get("frame"),
            fact.get("form_type"),
            fact.get("filed_date"),
            fact.get("accepted_at"),
            fact.get("fiscal_year"),
            fact.get("fiscal_period"),
            fact.get("accession_number"),
            fact.get("source_name"),
            fact["downloaded_at"],
        )
        for fact in facts
    ]
    with get_connection(database_path) as connection:
        connection.executemany(
            """
            DELETE FROM fundamental_facts
            WHERE filing_id = ?
              AND taxonomy = ?
              AND concept = ?
              AND COALESCE(unit, '') = COALESCE(?, '')
              AND COALESCE(period_start, '') = COALESCE(?, '')
              AND COALESCE(period_end, '') = COALESCE(?, '')
              AND COALESCE(value, 0.0) = COALESCE(?, 0.0)
            """,
            [
                (
                    fact["filing_id"],
                    fact["taxonomy"],
                    fact["concept"],
                    fact.get("unit"),
                    fact.get("period_start"),
                    fact.get("period_end"),
                    fact.get("value"),
                )
                for fact in facts
            ],
        )
        connection.executemany(
            """
            INSERT INTO fundamental_facts (
                filing_id, ticker, taxonomy, concept, standardized_field, unit, value, period_start,
                period_end, frame, form_type, filed_date, accepted_at, fiscal_year, fiscal_period,
                accession_number, source_name, downloaded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(filing_id, taxonomy, concept, unit, period_start, period_end, value) DO UPDATE SET
                standardized_field = excluded.standardized_field,
                frame = excluded.frame,
                form_type = excluded.form_type,
                filed_date = excluded.filed_date,
                accepted_at = excluded.accepted_at,
                fiscal_year = excluded.fiscal_year,
                fiscal_period = excluded.fiscal_period,
                accession_number = excluded.accession_number,
                source_name = excluded.source_name,
                downloaded_at = excluded.downloaded_at
            """,
            params,
        )
    return len(params)


def get_filings_for_ticker(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    form_types: Optional[Sequence[str]] = None,
    database_path=None,
) -> List[Dict[str, Any]]:
    """Return filing metadata for a ticker."""
    query = ["SELECT * FROM fundamental_filings WHERE ticker = ?"]
    params: List[Any] = [ticker.strip().upper()]
    if start_date:
        query.append("AND filing_date >= ?")
        params.append(start_date)
    if end_date:
        query.append("AND filing_date <= ?")
        params.append(end_date)
    if form_types:
        placeholders = ", ".join("?" for _ in form_types)
        query.append(f"AND form_type IN ({placeholders})")
        params.extend(form_types)
    query.append("ORDER BY filing_date, accepted_at, accession_number")
    with get_connection(database_path) as connection:
        rows = connection.execute("\n".join(query), params).fetchall()
    return [dict(row) for row in rows]


def get_facts_for_filing(filing_id: int, database_path=None) -> List[Dict[str, Any]]:
    """Return facts for one filing."""
    with get_connection(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM fundamental_facts WHERE filing_id = ? ORDER BY standardized_field, concept",
            (filing_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_facts_as_of(ticker: str, as_of_date: str, standardized_field: Optional[str] = None, database_path=None) -> List[Dict[str, Any]]:
    """Return facts publicly available on or before as_of_date."""
    query = [
        """
        SELECT f.*, fl.is_amendment, fl.report_period
        FROM fundamental_facts AS f
        JOIN fundamental_filings AS fl ON fl.filing_id = f.filing_id
        WHERE f.ticker = ?
          AND f.standardized_field IS NOT NULL
          AND COALESCE(f.accepted_at, f.filed_date) <= ?
        """
    ]
    params: List[Any] = [ticker.strip().upper(), as_of_date]
    if standardized_field:
        query.append("AND f.standardized_field = ?")
        params.append(standardized_field)
    query.append(
        """
        ORDER BY f.period_end DESC, COALESCE(f.accepted_at, f.filed_date) DESC,
                 fl.is_amendment DESC, f.accession_number DESC, f.concept
        """
    )
    with get_connection(database_path) as connection:
        rows = connection.execute("\n".join(query), params).fetchall()
    return [dict(row) for row in rows]


def get_fundamental_history(
    ticker: str,
    standardized_field: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    as_of_date: Optional[str] = None,
    database_path=None,
) -> List[Dict[str, Any]]:
    """Return historical facts for one standardized field."""
    query = [
        """
        SELECT f.*, fl.is_amendment
        FROM fundamental_facts AS f
        JOIN fundamental_filings AS fl ON fl.filing_id = f.filing_id
        WHERE f.ticker = ? AND f.standardized_field = ?
        """
    ]
    params: List[Any] = [ticker.strip().upper(), standardized_field]
    if start_date:
        query.append("AND f.period_end >= ?")
        params.append(start_date)
    if end_date:
        query.append("AND f.period_end <= ?")
        params.append(end_date)
    if as_of_date:
        query.append("AND COALESCE(f.accepted_at, f.filed_date) <= ?")
        params.append(as_of_date)
    query.append("ORDER BY f.period_end, COALESCE(f.accepted_at, f.filed_date), f.accession_number")
    with get_connection(database_path) as connection:
        rows = connection.execute("\n".join(query), params).fetchall()
    return [dict(row) for row in rows]


def count_fundamental_filings(ticker: Optional[str] = None, database_path=None) -> int:
    """Count fundamental filings."""
    with get_connection(database_path) as connection:
        if ticker:
            row = connection.execute("SELECT COUNT(*) AS c FROM fundamental_filings WHERE ticker = ?", (ticker.strip().upper(),)).fetchone()
        else:
            row = connection.execute("SELECT COUNT(*) AS c FROM fundamental_filings").fetchone()
    return int(row["c"])


def count_fundamental_facts(ticker: Optional[str] = None, database_path=None) -> int:
    """Count fundamental facts."""
    with get_connection(database_path) as connection:
        if ticker:
            row = connection.execute("SELECT COUNT(*) AS c FROM fundamental_facts WHERE ticker = ?", (ticker.strip().upper(),)).fetchone()
        else:
            row = connection.execute("SELECT COUNT(*) AS c FROM fundamental_facts").fetchone()
    return int(row["c"])


def fundamentals_status(database_path=None) -> Dict[str, Any]:
    """Return aggregate fundamentals status for CLI display."""
    with get_connection(database_path) as connection:
        mapped = connection.execute("SELECT COUNT(*) AS c FROM sec_ticker_map").fetchone()["c"]
        filings = connection.execute(
            "SELECT COUNT(*) AS c, MIN(filing_date) AS earliest, MAX(filing_date) AS latest, MAX(accepted_at) AS latest_accepted FROM fundamental_filings"
        ).fetchone()
        facts = connection.execute("SELECT COUNT(*) AS c FROM fundamental_facts").fetchone()["c"]
        by_ticker = connection.execute(
            """
            SELECT fl.ticker, COUNT(DISTINCT fl.filing_id) AS filings, COUNT(ff.fact_id) AS facts
            FROM fundamental_filings AS fl
            LEFT JOIN fundamental_facts AS ff ON ff.filing_id = fl.filing_id
            GROUP BY fl.ticker
            ORDER BY fl.ticker
            """
        ).fetchall()
    return {
        "mapped_securities": int(mapped),
        "filing_count": int(filings["c"]),
        "fact_count": int(facts),
        "earliest_filing_date": filings["earliest"],
        "latest_filing_date": filings["latest"],
        "latest_accepted_at": filings["latest_accepted"],
        "by_ticker": {row["ticker"]: {"filings": int(row["filings"]), "facts": int(row["facts"])} for row in by_ticker},
    }
