---
type: Exposure
title: Texwin Houston warehouse
description: Mock. $38M TIV, FEMA zone AE, Harris County.
tags: [exposure, texas, flood, warehouse]
tiv: 38000000
flood_zone: AE
county: Harris
miles_to_coast: 25
sources:
  - id: ds-exposure
    title: Risk exposure datastore (mock)
---

# Texwin Houston warehouse

| Field | Value |
|---|---|
| Address | Houston, TX (Harris County) |
| TIV | $38M |
| Occupancy | Industrial warehouse, ESFR, masonry |
| Flood | Zone AE |
| Elevation cert | Missing |
| Distance to Gulf | 25 miles (outside 20-mile named-storm band) |

## Company

* [Texwin Acquisitions LLC](../companies/texwin-acquisitions.md)

## IKE that fires

* [IKE-UW-124 Warehouse](../ike/occupancy-warehouse.md) — under $40M building cap
* [IKE-UW-310 Flood zone AE](../ike/flood-zone-ae.md) — AE above $10M is referral; no elevation cert
* [IKE-UW-510 Harris County accumulation](../ike/harris-county-accumulation.md)
* [Harris County accumulation](accum-harris-county.md)
