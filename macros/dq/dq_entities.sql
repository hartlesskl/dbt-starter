{#-
  The one list of registered entities. The three cross-entity data quality models
  (dq_rule_failures, dq_quarantine_summary and dq_rule_catalog) read every entity from here, so
  adding an entity to the list is the only edit they need.

  Each entry is {entity, relation, row_key, rules}: a short name, the bronze model that carries
  one flag column per rule, the column that keys a row, and the entity's rule registry.
  Register your entities between the markers below, and delete the example entries when you
  swap in your own sources.
-#}
{% macro dq_entities() %}
  {%- set entities = [] -%}
  {#- example:begin -#}
  {%- do entities.append({'entity': 'taxi_zones', 'relation': ref('bronze__nyc_taxi__taxi_zones'), 'row_key': 'zone_key', 'rules': rules__nyc_taxi__taxi_zones()}) -%}
  {%- do entities.append({'entity': 'yellow_trips', 'relation': ref('bronze__nyc_taxi__yellow_trips'), 'row_key': 'trip_key', 'rules': rules__nyc_taxi__yellow_trips()}) -%}
  {#- example:end -#}
  {{- return(entities) -}}
{% endmacro %}
