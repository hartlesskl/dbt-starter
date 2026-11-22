{#- Quarantine rules for yellow trips. Codes start with TRIP_ and never change once released.
    D-01 rule of thumb: error means the row is unusable, warn means a real value that looks
    suspicious. Error rules move a row to quarantine. Warn rules never do: they only count in
    dq_warning_count and dq_warning_codes. Every rule carries a `rationale`: the data problem it
    catches and its known benign causes, in plain words (ASCII only, no apostrophes). A rationale
    never blames a driver, vendor or passenger.

    The order below is the order of the codes in dq_reason_codes: unparseable text, missing
    values, unknown codes and zones, the time and duplicate rules, then the warn rules.

    The 24-hour limit on a trip is written once, in nyc_taxi__max_trip_seconds(). The rule, its
    description and its rationale all read it, and the silver duration bound calls the same
    macro, so the limit and its description cannot drift (D-03).

    NULL decisions, one per group of rules:
    - The UNPARSEABLE rules fire only when the raw text is present and the typed value is NULL
      (on_null pass), so an empty cell is never UNPARSEABLE and each failure has one cause.
    - The MISSING rules read the raw column, so a landed NULL (what an empty cell becomes) fails
      (on_null fail), and an unconvertible value is left to its UNPARSEABLE rule.
    - The UNKNOWN rules pass a NULL id (on_null pass). All 140,162 Flex Fare rows of 2024-01
      carry payment type 0 with a NULL rate code and NULL passenger count, and a NULL key also
      passes a relationships test later. A missing zone has its own MISSING rule.
    - TRIP_PICKUP_OUT_OF_MONTH passes a NULL pickup (the MISSING rule owns it), but fails every
      row whose file name holds no month, because then the month cannot be checked.
    - TRIP_DROPOFF_BEFORE_PICKUP and TRIP_ZERO_LENGTH compare the two typed timestamps directly
      (no arithmetic, so no overflow). An unknown comparison passes (on_null pass): the missing
      or unconvertible value has its own rule.
    - TRIP_DURATION_TOO_LONG reads the duration, which bronze computes from timestamps clamped
      to 2000-01-01 and 2050-01-01, so it never overflows a T-SQL int. A NULL duration passes.
    - TRIP_EXACT_DUPLICATE fails every copy after the first (duplicate_rank > 1).
    - Every warn rule passes a NULL value: a NULL amount or count is not a suspicious value.
    - store_and_fwd_flag has no rule: the data dictionary defines Y and N and nothing is lost
      when another value passes. -#}

{#- The one place the longest allowed trip is written, in seconds. -#}
{% macro nyc_taxi__max_trip_seconds() %}
  {{- return(86400) -}}
{% endmacro %}

{% macro rules__nyc_taxi__yellow_trips() %}
  {% set max_trip_seconds = nyc_taxi__max_trip_seconds() %}
  {% set max_trip_hours = max_trip_seconds // 3600 %}
  {% set int_cause = 'The published files hold whole numbers here, so text that does not convert points at a damaged file or a changed file layout.' %}
  {% set dec_cause = 'The published files hold plain numbers here, so text that does not convert points at a damaged file or a changed file layout.' %}
  {% set time_cause = 'The published files hold timestamps here, so text that does not convert points at a damaged file or a changed file layout.' %}
  {{- return([
    starter.cast_check_rule('TRIP_VENDOR_ID_UNPARSEABLE', 'vendor_id__raw', 'vendor_id', 'vendor id is not a whole number', 'The vendor id could not be read as a whole number. ' ~ int_cause),
    starter.cast_check_rule('TRIP_PICKUP_DATETIME_UNPARSEABLE', 'pickup_datetime__raw', 'pickup_datetime', 'pickup time is not a valid timestamp', 'The pickup time could not be read as a timestamp, which includes dates that do not exist. ' ~ time_cause),
    starter.cast_check_rule('TRIP_DROPOFF_DATETIME_UNPARSEABLE', 'dropoff_datetime__raw', 'dropoff_datetime', 'dropoff time is not a valid timestamp', 'The dropoff time could not be read as a timestamp, which includes dates that do not exist. ' ~ time_cause),
    starter.cast_check_rule('TRIP_PASSENGER_COUNT_UNPARSEABLE', 'passenger_count__raw', 'passenger_count', 'passenger count is not a whole number', 'The passenger count could not be read as a whole number. ' ~ int_cause),
    starter.cast_check_rule('TRIP_DISTANCE_UNPARSEABLE', 'trip_distance__raw', 'trip_distance', 'trip distance is not a number', 'The trip distance could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_RATE_CODE_UNPARSEABLE', 'rate_code_id__raw', 'rate_code_id', 'rate code is not a whole number', 'The rate code could not be read as a whole number. ' ~ int_cause),
    starter.cast_check_rule('TRIP_PU_LOCATION_UNPARSEABLE', 'pu_location_id__raw', 'pu_location_id', 'pickup zone id is not a whole number', 'The pickup zone id could not be read as a whole number. ' ~ int_cause),
    starter.cast_check_rule('TRIP_DO_LOCATION_UNPARSEABLE', 'do_location_id__raw', 'do_location_id', 'dropoff zone id is not a whole number', 'The dropoff zone id could not be read as a whole number. ' ~ int_cause),
    starter.cast_check_rule('TRIP_PAYMENT_TYPE_UNPARSEABLE', 'payment_type__raw', 'payment_type', 'payment type is not a whole number', 'The payment type could not be read as a whole number. ' ~ int_cause),
    starter.cast_check_rule('TRIP_FARE_AMOUNT_UNPARSEABLE', 'fare_amount__raw', 'fare_amount', 'fare amount is not a number', 'The fare amount could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_EXTRA_UNPARSEABLE', 'extra__raw', 'extra', 'extra charge is not a number', 'The extra charge could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_MTA_TAX_UNPARSEABLE', 'mta_tax__raw', 'mta_tax', 'MTA tax is not a number', 'The MTA tax could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_TIP_AMOUNT_UNPARSEABLE', 'tip_amount__raw', 'tip_amount', 'tip amount is not a number', 'The tip amount could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_TOLLS_AMOUNT_UNPARSEABLE', 'tolls_amount__raw', 'tolls_amount', 'tolls amount is not a number', 'The tolls amount could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_IMPROVEMENT_SURCHARGE_UNPARSEABLE', 'improvement_surcharge__raw', 'improvement_surcharge', 'improvement surcharge is not a number', 'The improvement surcharge could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_TOTAL_AMOUNT_UNPARSEABLE', 'total_amount__raw', 'total_amount', 'total amount is not a number', 'The total amount could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_CONGESTION_SURCHARGE_UNPARSEABLE', 'congestion_surcharge__raw', 'congestion_surcharge', 'congestion surcharge is not a number', 'The congestion surcharge could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_AIRPORT_FEE_UNPARSEABLE', 'airport_fee__raw', 'airport_fee', 'airport fee is not a number', 'The airport fee could not be read as a number. ' ~ dec_cause),
    starter.cast_check_rule('TRIP_CBD_CONGESTION_FEE_UNPARSEABLE', 'cbd_congestion_fee__raw', 'cbd_congestion_fee', 'CBD congestion fee is not a number', 'The CBD congestion fee could not be read as a number. ' ~ dec_cause),
    {
      'code': 'TRIP_PICKUP_DATETIME_MISSING',
      'description': 'pickup time is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'Without a pickup time a trip cannot be placed in a month or given a duration.',
      'fail_when': 'pickup_datetime__raw is null'
    },
    {
      'code': 'TRIP_DROPOFF_DATETIME_MISSING',
      'description': 'dropoff time is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'Without a dropoff time a trip has no end and no duration, so it cannot be compared with its pickup.',
      'fail_when': 'dropoff_datetime__raw is null'
    },
    {
      'code': 'TRIP_PU_LOCATION_MISSING',
      'description': 'pickup zone is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'Without a pickup zone a trip cannot be joined to the zone lookup or counted by area.',
      'fail_when': 'pu_location_id__raw is null'
    },
    {
      'code': 'TRIP_DO_LOCATION_MISSING',
      'description': 'dropoff zone is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'Without a dropoff zone a trip cannot be joined to the zone lookup or counted by area.',
      'fail_when': 'do_location_id__raw is null'
    },
    {
      'code': 'TRIP_PU_LOCATION_UNKNOWN',
      'description': 'pickup zone is not a valid zone',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'The pickup zone id is not among the valid zones of the zone lookup (zones 264 and 265 are valid, they are the published unknown and outside-NYC zones), so the trip cannot be placed in an area.',
      'fail_when': 'pu_location_id is not null and pu_zone_found = 0'
    },
    {
      'code': 'TRIP_DO_LOCATION_UNKNOWN',
      'description': 'dropoff zone is not a valid zone',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'The dropoff zone id is not among the valid zones of the zone lookup (zones 264 and 265 are valid, they are the published unknown and outside-NYC zones), so the trip cannot be placed in an area.',
      'fail_when': 'do_location_id is not null and do_zone_found = 0'
    },
    {
      'code': 'TRIP_VENDOR_ID_UNKNOWN',
      'description': 'vendor id is not in the vendor list',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'The vendor code is not in the TLC data dictionary dated 2025-03-18 (the vendors seed). A code the TLC adds later is added to the seed, which re-releases these rows on the next build.',
      'fail_when': 'vendor_id is not null and vendor_found = 0'
    },
    {
      'code': 'TRIP_RATE_CODE_UNKNOWN',
      'description': 'rate code is not in the rate code list',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'The rate code is not in the TLC data dictionary dated 2025-03-18 (the rate codes seed; 99 is the published null or unknown code). A NULL rate code passes because Flex Fare trips carry none. A code the TLC adds later is added to the seed, which re-releases these rows.',
      'fail_when': 'rate_code_id is not null and rate_code_found = 0'
    },
    {
      'code': 'TRIP_PAYMENT_TYPE_UNKNOWN',
      'description': 'payment type is not in the payment type list',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'The payment type is not in the TLC data dictionary dated 2025-03-18 (the payment types seed; 0 is a Flex Fare trip, 6 a voided trip). A code the TLC adds later is added to the seed, which re-releases these rows.',
      'fail_when': 'payment_type is not null and payment_type_found = 0'
    },
    {
      'code': 'TRIP_PICKUP_OUT_OF_MONTH',
      'description': 'pickup time is outside the month of its file',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'Each file holds one month, but some rows carry pickups years away, such as 2001 or 2088 in a 2019-01 file (18 rows in 2024-01). That is usually a device clock error, and the row would sit in the wrong month of every report. A file name with no month fails every row, because the month cannot be checked.',
      'fail_when': 'pickup_datetime is not null and (source_month is null or pickup_month <> source_month)'
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
      'code': 'TRIP_DURATION_TOO_LONG',
      'description': 'trip lasts longer than ' ~ max_trip_hours ~ ' hours',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'A trip longer than ' ~ max_trip_hours ~ ' hours (16 rows in 2024-01) is usually a meter left running or a device clock error, not a real ride, and it would distort every average duration. A trip of exactly ' ~ max_trip_hours ~ ' hours passes.',
      'fail_when': 'trip_duration_seconds > ' ~ max_trip_seconds
    },
    {
      'code': 'TRIP_EXACT_DUPLICATE',
      'description': 'row repeats an earlier row exactly',
      'severity': 'error',
      'on_null': 'pass',
      'rationale': 'Two rows with identical text in every landed data column are one trip published twice, for example in two monthly files. The copy in its own month is kept and every other copy is quarantined, so the trip is counted once.',
      'fail_when': 'duplicate_rank > 1'
    },
    {
      'code': 'TRIP_ZERO_LENGTH',
      'description': 'dropoff time equals pickup time',
      'severity': 'warn',
      'on_null': 'pass',
      'rationale': 'A dropoff equal to the pickup (814 rows in 2024-01) usually marks a cancelled or test trip, so the row is real but suspicious and stays in silver with a warning.',
      'fail_when': 'dropoff_datetime = pickup_datetime'
    },
    {
      'code': 'TRIP_ZERO_DISTANCE',
      'description': 'trip distance is zero',
      'severity': 'warn',
      'on_null': 'pass',
      'rationale': 'A distance of zero (60,371 rows in 2024-01) can be a cancelled ride, a trip that never left the pickup point or a meter fault, so the row is real but suspicious and stays in silver with a warning.',
      'fail_when': 'trip_distance = 0'
    },
    {
      'code': 'TRIP_ZERO_TOTAL',
      'description': 'total amount is zero',
      'severity': 'warn',
      'on_null': 'pass',
      'rationale': 'A total of zero (416 rows in 2024-01) is usually a no-charge ride, a void or a test trip, so the row is real but suspicious and stays in silver with a warning.',
      'fail_when': 'total_amount = 0'
    },
    {
      'code': 'TRIP_NEGATIVE_FARE',
      'description': 'fare amount is negative',
      'severity': 'warn',
      'on_null': 'pass',
      'rationale': 'A negative fare (37,448 rows in 2024-01) is usually a refund, a dispute or a voided payment recorded as a second row, so the row is real but suspicious and stays in silver with a warning.',
      'fail_when': 'fare_amount < 0'
    },
    {
      'code': 'TRIP_NEGATIVE_TOTAL',
      'description': 'total amount is negative',
      'severity': 'warn',
      'on_null': 'pass',
      'rationale': 'A negative total (35,504 rows in 2024-01) is usually a refund, a dispute or a voided payment recorded as a second row, so the row is real but suspicious and stays in silver with a warning.',
      'fail_when': 'total_amount < 0'
    },
    {
      'code': 'TRIP_PASSENGER_COUNT_ZERO',
      'description': 'passenger count is zero',
      'severity': 'warn',
      'on_null': 'pass',
      'rationale': 'A passenger count of zero (31,465 rows in 2024-01) is usually a count that was left unset on the meter. A NULL count is left alone because Flex Fare rows carry none. The row stays in silver with a warning.',
      'fail_when': 'passenger_count = 0'
    }
  ]) -}}
{% endmacro %}
