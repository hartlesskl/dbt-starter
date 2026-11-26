# Guides

These guides explain the conventions this template follows and how to adopt it for your own data.
The template moves public data through landing, bronze, silver and gold, quarantines bad rows, and
tests each step. The guides show what each convention is for and how to keep it when you swap in
your own sources.

Each guide is added to this list as it is written. For now, these pages already exist:

- [Conventions](conventions.md): the eight rules the template enforces, with the reason, the decision record and the check for each.
- [Data terms and credit](../data-terms.md): how a source declares its terms and credit line.
- [Setting up Microsoft Fabric](../fabric-setup.md): the Lakehouse target and its configuration.
- [The Fabric native dbt job](../fabric-native-job.md): how the template runs inside Fabric's dbt job.
