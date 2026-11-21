select
    zone_key,
    _source_file,
    _source_row_number,
    _batch_id,
    _loaded_at,
    location_id__raw,
    location_id,
    borough,
    zone,
    service_zone,
    dq_fail_zone_location_id_unparseable,
    dq_fail_zone_location_id_missing,
    dq_fail_zone_borough_missing,
    dq_fail_zone_borough_unknown,
    dq_fail_zone_service_zone_unknown,
    dq_fail_zone_location_id_duplicate,
    dq_failed_rule_count,
    dq_reason_codes
from {{ ref('bronze__nyc_taxi__taxi_zones') }}
where dq_is_valid = 0
