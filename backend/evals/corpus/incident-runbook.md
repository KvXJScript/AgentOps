# Incident Response Runbook v3

Synthetic evaluation document for AgentOps. Current version, effective from January 2026.

## Severity levels

SEV1 means a full outage or data loss affecting customers. SEV2 means a degraded core feature.
SEV3 is a minor issue with a workaround.

## Response times

For a SEV1 incident the on-call engineer must acknowledge the page within 15 minutes and open an incident
channel. SEV2 must be acknowledged within one hour. SEV3 is handled during business hours.

## Alerts

Alert PAGER-7731 means database replication lag above 30 seconds. Check the replica health dashboard first,
then fail over to the standby if the lag keeps growing.

## Postmortems

A blameless postmortem is required for every SEV1 and SEV2 incident. It must be published within five business
days and list at least one follow-up action with an owner.
