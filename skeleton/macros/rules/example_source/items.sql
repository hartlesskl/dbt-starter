{#- Quarantine rules for the placeholder items. Codes start with ITEM_ and never change once
    released. id is checked twice, once for a missing value and once for a value that is not a
    whole number, so each failure has one cause. New rules are appended: the order of the codes
    in dq_reason_codes follows this list. -#}
{% macro rules__example_source__items() %}
  {{- return([
    {
      'code': 'ITEM_ID_MISSING',
      'description': 'id is NULL',
      'severity': 'error',
      'on_null': 'fail',
      'rationale': 'An item with no id cannot be keyed or matched from anywhere else, so it would sit in the table and join to nothing.',
      'fail_when': 'id__raw is null'
    },
    starter.cast_check_rule(
      'ITEM_ID_UNPARSEABLE',
      'id__raw',
      'item_id',
      'id text is present but is not an integer',
      'An id that is not a whole number cannot key an item, usually because the file was edited by hand or exported with a stray character.'
    ),
    starter.cast_check_rule(
      'ITEM_AMOUNT_UNPARSEABLE',
      'amount__raw',
      'amount',
      'amount text is present but is not a number',
      'An amount that is not a number cannot be summed or compared, usually because the file holds a word or a stray symbol where a figure belongs.'
    ),
    starter.cast_check_rule(
      'ITEM_UPDATED_AT_UNPARSEABLE',
      'updated_at__raw',
      'updated_at',
      'updated_at text is present but is not a timestamp',
      'A timestamp that does not parse cannot be ordered or aged, usually because it carries a zone designator or a format the parse does not accept.'
    )
  ]) -}}
{% endmacro %}
