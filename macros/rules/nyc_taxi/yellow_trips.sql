{#- Quarantine rules for yellow trips. Codes start with TRIP_ and never change once released.
    D-01 rule of thumb: error means the row is unusable, warn means a real value that looks
    suspicious. The first two rules are errors and TRIP_ZERO_LENGTH is a warn rule: it never
    moves a row to quarantine, it only counts in dq_warning_count and dq_warning_codes. Every
    rule carries a `rationale`: the data problem it catches, in plain words (ASCII only, no
    apostrophes).

    NULL decisions, one per rule:
    - TRIP_PICKUP_DATETIME_MISSING reads the raw column, so a landed NULL (what an empty cell
      becomes) fails (on_null fail) and an unconvertible text value is left to the cast checks
      that later plans add, so each failure has one cause.
    - TRIP_DROPOFF_BEFORE_PICKUP compares the two typed timestamps directly. If either one is
      NULL the comparison is unknown, and an unknown comparison passes (on_null pass): the
      missing or unconvertible value has its own rule.
    - TRIP_ZERO_LENGTH compares the same two unclamped typed timestamps (no arithmetic, so no
      overflow). An unknown comparison passes (on_null pass) for the same reason.

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
    },
    {
      'code': 'TRIP_ZERO_LENGTH',
      'description': 'dropoff time equals pickup time',
      'severity': 'warn',
      'on_null': 'pass',
      'rationale': 'A dropoff equal to the pickup (814 rows in 2024-01) usually marks a cancelled or test trip, so the row is real but suspicious and stays in silver with a warning.',
      'fail_when': 'dropoff_datetime = pickup_datetime'
    }
  ]) -}}
{% endmacro %}
