{#- Quarantine rules for yellow trips. Codes start with TRIP_ and never change once released.
    D-01 rule of thumb: error means the row is unusable, warn means a real value that looks
    suspicious. Both rules here are errors. Every rule carries a `rationale`: the data problem
    it catches, in plain words (ASCII only, no apostrophes).

    NULL decisions, one per rule:
    - TRIP_PICKUP_DATETIME_MISSING reads the raw column, so a landed NULL (what an empty cell
      becomes) fails (on_null fail) and an unconvertible text value is left to the cast checks
      that later plans add, so each failure has one cause.
    - TRIP_DROPOFF_BEFORE_PICKUP compares the two typed timestamps directly. If either one is
      NULL the comparison is unknown, and an unknown comparison passes (on_null pass): the
      missing or unconvertible value has its own rule.

    New rules are inserted in their final positions by later plans: the order of the codes in
    dq_reason_codes follows this list. -#}
{% macro rules__nyc_taxi__yellow_trips() %}
  {{- return([
    {
      'code': 'TRIP_PICKUP_DATETIME_MISSING',
      'description': 'pickup time is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'Without a pickup time a trip cannot be placed in a month or given a duration.',
      'fail_when': 'pickup_datetime__raw is null'
    },
    {
      'code': 'TRIP_DROPOFF_BEFORE_PICKUP',
      'description': 'dropoff time is earlier than pickup time',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'The published files carry dropoff times earlier than the pickup (56 rows in 2024-01), usually a device clock error, which would give a negative duration.',
      'fail_when': 'dropoff_datetime < pickup_datetime'
    }
  ]) -}}
{% endmacro %}
