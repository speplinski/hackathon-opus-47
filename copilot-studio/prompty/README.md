# Prompty

Wygenerowane z promptów potoku w Pythonie (`narzedzia/generuj_prompty.py`); nie edytuj ręcznie.
Treść instrukcji jest identyczna z promptem systemowym z badania, z dopisaną sekcją INPUT.

| Nr | Prompt | Warstwa | Model w badaniu | Skrypt kernela | Buduje wejście | Czyta wyjście |
|---|---|---|---|---|---|---|
| 01 | [AD_L1_Klasyfikacja](01_AD_L1_Klasyfikacja.md) | L1 | `claude-opus-4-6` | `AD_Wejscie` | `l1_messages` | `l1_parse` |
| 02 | [AD_L2_GrafSkargi](02_AD_L2_GrafSkargi.md) | L2 | `claude-opus-4-7` | `AD_Wejscie` | `l2_messages` | `l2_extract` |
| 03 | [AD_L3_Grupowanie](03_AD_L3_Grupowanie.md) | L3 | `— (w badaniu: MiniLM + HDBSCAN, bez modelu językowego)` | `AD_Wejscie` | `l3_message` | `l3_clusters` |
| 04 | [AD_L3b_Etykieta](04_AD_L3b_Etykieta.md) | L3b | `claude-haiku-4-5-20251001` | `AD_Wejscie` | `l3b_message` | `l3b_label` |
| 05 | [AD_L4_Norman](05_AD_L4_Norman.md) | L4 | `claude-sonnet-4-6` | `AD_Audyt` | `l4_message` | `l4_verdict` |
| 06 | [AD_L4_WCAG](06_AD_L4_WCAG.md) | L4 | `claude-sonnet-4-6` | `AD_Audyt` | `l4_message` | `l4_verdict` |
| 07 | [AD_L4_Kahneman](07_AD_L4_Kahneman.md) | L4 | `claude-sonnet-4-6` | `AD_Audyt` | `l4_message` | `l4_verdict` |
| 08 | [AD_L4_Osterwalder](08_AD_L4_Osterwalder.md) | L4 | `claude-sonnet-4-6` | `AD_Audyt` | `l4_message` | `l4_verdict` |
| 09 | [AD_L4_Cooper](09_AD_L4_Cooper.md) | L4 | `claude-sonnet-4-6` | `AD_Audyt` | `l4_message` | `l4_verdict` |
| 10 | [AD_L4_Garrett](10_AD_L4_Garrett.md) | L4 | `claude-sonnet-4-6` | `AD_Audyt` | `l4_message` | `l4_verdict` |
| 11 | [AD_L5_Uzgodnienie](11_AD_L5_Uzgodnienie.md) | L5 | `claude-opus-4-7` | `AD_Uzgodnienie` | `l5_message` | `l5_reconcile` |
| 12 | [AD_L6_Priorytet](12_AD_L6_Priorytet.md) | L6 | `claude-opus-4-7` | `AD_Uzgodnienie` | `l6_message` | `l6_score` |
| 13 | [AD_L7_Decyzja](13_AD_L7_Decyzja.md) | L7 | `claude-opus-4-7` | `AD_Uzgodnienie` | `l7_message` | `l7_decide` |
| 14 | [AD_L8_Ocena](14_AD_L8_Ocena.md) | L8 | `claude-opus-4-7` | `AD_Petla` | `l8_baseline / l8_tweak_parse` | `l8_proposed / l8_loop_step` |
| 15 | [AD_L8_Poprawka](15_AD_L8_Poprawka.md) | L8 | `claude-opus-4-7` | `AD_Petla` | `l8_loop_plan` | `l8_tweak_parse` |
| 16 | [AD_Weryfikacja](16_AD_Weryfikacja.md) | Weryfikacja | `claude-opus-4-7` | `AD_Petla` | `vp_message` | `vp_parse` |
