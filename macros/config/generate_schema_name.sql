{#- Use the layer's schema name as written (bronze, silver, gold), without prefixing the
    target schema. dbt only honours this override from the root project. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- if custom_schema_name is none -%}
    {{ target.schema }}
  {%- else -%}
    {{ custom_schema_name | trim }}
  {%- endif -%}
{%- endmacro %}
