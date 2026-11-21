{#- Quarantine rules for the zone lookup. Codes start with ZONE_ and never change once released.
    Known-values rules (borough and service zone lists) arrive with plan 01-05. -#}
{% macro rules__nyc_taxi__taxi_zones() %}
  {{- return([
    starter.cast_check_rule(
      'ZONE_LOCATION_ID_UNPARSEABLE',
      'location_id__raw',
      'location_id',
      'location_id text is present but is not an integer'
    ),
    {
      'code': 'ZONE_LOCATION_ID_MISSING',
      'description': 'location_id is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'fail_when': 'location_id__raw is null'
    },
    {
      'code': 'ZONE_BOROUGH_MISSING',
      'description': 'borough is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'fail_when': 'borough is null'
    }
  ]) -}}
{% endmacro %}
