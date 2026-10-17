"""Shared first-isolate ranking; inputs are application-owned SQL fragments."""


def ranked_isolates_sql(conditions: list[str], *, by_ward: bool = False,
                        split_period: bool = False, partition_expressions: tuple[str, ...] = (),
                        extra_columns: str = "", joins: str = "") -> str:
    period = "CASE WHEN c.sample_date >= :current_start THEN 'current' ELSE 'baseline' END"
    groups = ["c.patient_id", "i.organism", "c.specimen"]
    if by_ward:
        groups.append("c.ward_code")
    if split_period:
        groups.append(period)
    groups.extend(partition_expressions)
    return f"""
        SELECT i.isolate_id, c.patient_id, c.ward_code, i.organism, c.specimen,
               {extra_columns + ',' if extra_columns else ''}
               {period if split_period else "'period'"} AS reporting_period,
               ROW_NUMBER() OVER (
                   PARTITION BY {','.join(groups)}
                   ORDER BY c.sample_date, c.culture_id, i.isolate_no, i.isolate_id
               ) AS isolate_rank
        FROM cultures AS c JOIN isolates AS i ON i.culture_id = c.culture_id
        {joins}
        WHERE {' AND '.join(conditions)}
    """
