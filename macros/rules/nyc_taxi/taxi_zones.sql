{#- Quarantine rules for the zone lookup. Codes start with ZONE_ and never change once released.
    The two known-values rules read borough_found and service_zone_found, which bronze computes
    from the known-values seeds, so every predicate here stays row-local. NULL borough is left
    to ZONE_BOROUGH_MISSING and service_zone is optional, so each failure has one cause.
    ZONE_LOCATION_ID_DUPLICATE reads location_id_count, a window count that bronze computes, so
    every row that shares an id is quarantined together and silver never holds a repeated id.
    New rules are appended: the order of the codes in dq_reason_codes follows this list. -#}
{% macro rules__nyc_taxi__taxi_zones() %}
  {{- return([
    starter.cast_check_rule(
      'ZONE_LOCATION_ID_UNPARSEABLE',
      'location_id__raw',
      'location_id',
      'location_id text is present but is not an integer',
      'An id that is not a whole number cannot key a zone or be matched from a trip, usually because the file was edited by hand or exported with a stray character.'
    ),
    {
      'code': 'ZONE_LOCATION_ID_MISSING',
      'description': 'location_id is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'A zone row with no id cannot be joined from trips, so it would sit in the lookup and match nothing.',
      'fail_when': 'location_id__raw is null'
    },
    {
      'code': 'ZONE_BOROUGH_MISSING',
      'description': 'borough is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'A zone with an empty borough cannot be grouped or reported by borough, which usually means a cell was left blank in the source file.',
      'fail_when': 'borough is null'
    },
    {
      'code': 'ZONE_BOROUGH_UNKNOWN',
      'description': 'borough is not in the known-boroughs seed',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'A borough outside the published list is usually a spelling or case change, and it would split the borough counts into two groups that mean the same thing.',
      'fail_when': 'borough is not null and borough_found = 0'
    },
    {
      'code': 'ZONE_SERVICE_ZONE_UNKNOWN',
      'description': 'service_zone is not in the known-service-zones seed',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'A service zone outside the published list is usually a spelling or case change, and it would split the service zone counts into two groups that mean the same thing.',
      'fail_when': 'service_zone is not null and service_zone_found = 0'
    },
    {
      'code': 'ZONE_LOCATION_ID_DUPLICATE',
      'description': 'location_id appears on more than one row',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'Two rows sharing an id can disagree about the borough or the zone name, so neither row is trusted and both are held back until someone decides which one is right.',
      'fail_when': 'location_id is not null and location_id_count > 1'
    }
  ]) -}}
{% endmacro %}
