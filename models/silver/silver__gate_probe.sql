-- Throwaway probe for plan 02-10: refs a model that does not exist so the gate fails at parse.
select * from {{ ref('this_model_does_not_exist') }}
