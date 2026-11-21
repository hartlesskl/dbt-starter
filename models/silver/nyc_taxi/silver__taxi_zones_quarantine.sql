select
    zone_key,
    _source_file,
    _source_row_number,
    location_id__raw,
    borough,
    zone,
    service_zone,
    dq_failed_rule_count,
    dq_reason_codes
from {{ ref('bronze__nyc_taxi__taxi_zones') }}
where dq_is_valid = 0
