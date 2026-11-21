{#- Model files start with their layer (bronze__, silver__, gold__). The table name drops that
    prefix, so bronze__<source>__<entity> becomes bronze.<source>__<entity> and
    gold__dim_zone becomes gold.dim_zone. Only models are renamed; seeds keep their name. -#}
{% macro generate_alias_name(custom_alias_name=none, node=none) -%}
  {%- if custom_alias_name -%}
    {{ custom_alias_name | trim }}
  {%- else -%}
    {%- set parts = node.name.split('__') -%}
    {%- if node.resource_type == 'model' and parts | length > 1 and parts[0] in ['bronze', 'silver', 'gold'] -%}
      {{ parts[1:] | join('__') }}
    {%- else -%}
      {{ node.name }}
    {%- endif -%}
  {%- endif -%}
{%- endmacro %}
