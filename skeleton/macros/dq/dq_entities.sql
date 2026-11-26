{#-
  The one list of registered entities. The three cross-entity data quality models
  (dq_rule_failures, dq_quarantine_summary and dq_rule_catalog) read every entity from here, so
  adding an entity to the list is the only edit they need.

  Each entry is {entity, relation, row_key, rules}: a short name, the bronze model that carries
  one flag column per rule, the column that keys a row, and the entity's rule registry.
  Register your entities here, one `do entities.append` line each. The first entry is the
  placeholder source, so replace it with your own when you swap in your sources.
-#}
{% macro dq_entities() %}
  {%- set entities = [] -%}
  {%- do entities.append({'entity': 'items', 'relation': ref('bronze__example_source__items'), 'row_key': 'item_key', 'rules': rules__example_source__items()}) -%}
  {{- return(entities) -}}
{% endmacro %}
