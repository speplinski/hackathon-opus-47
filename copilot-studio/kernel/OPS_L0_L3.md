# AD_Wejscie i AD_Dowody: operacje

Wywołanie jak w pozostałych skryptach: Excel Online (Business) → **Run script**, `operation` (tekst), `payload` (tekst JSON). Wynik: `{ok, operation, result, errors}`. `ok:false` oznacza błędne wejście. Odpowiedź modelu nie do użycia to `ok:true` ze statusem `fallback`, `quarantine` albo `error` w wyniku.

## AD_Wejscie (L0–L3b, tryb B)

| Operacja | Payload | Wynik | Krok flow |
|---|---|---|---|
| `l0_normalize` | `{sources: [{source_type, items: [{id?, text, date?, rating?, segment?, source_ref?}]}]}` | `{utterances: Utterance[], rejected: [{source_type, index, reason}]}` | po wczytaniu `wypowiedzi.json` |
| `l1_messages` | `{utterances}` | `[{utterance_id, message}]` | przed promptem `AD_L1_Klasyfikacja` |
| `l1_parse` | `{items: [{utterance_id, response_text}]}` | `[{utterance_id, ok, is_ux_relevant, classifier_confidence, rubric_tags, error}]` | po wszystkich wywołaniach L1, jednym wywołaniem |
| `l2_messages` | `{utterances}` | `[{utterance_id, message}]` | przed promptem `AD_L2_GrafSkargi` |
| `l2_extract` | `{items: [{utterance, response_text}]}` | `{graphs, quarantine, errors}` | po wszystkich wywołaniach L2 |
| `l3_message` | `{graphs}` | `{node_count, nodes, message}` | przed promptem `AD_L3_Grupowanie` |
| `l3_clusters` | `{nodes, response_text, min_cluster_size?}` (5) | `{status: clustered\|fallback, reason, clusters: InsightCluster[], noise_idxs, issues}` | po grupowaniu |
| `l3b_message` | `{cluster}` | `{cluster_id, message}` | przed promptem `AD_L3b_Etykieta` |
| `l3b_label` | `{cluster, response_text}` | `{status: labeled\|fallback, reason, cluster}` | po etykiecie |

**L0.** Dowolny typ źródła (`app_review`, `nps_comment`, `interview`, `survey`, `complaint`…). Brak `id` → identyfikator `<typ>-<FNV-1a tekstu>`, ten sam przy ponownym przebiegu. Puste teksty i powtórzone identyfikatory trafiają do `rejected`.

**L1.** Wiadomość `rating: N\n<user_review id="…">…</user_review>`; dla źródeł bez oceny `rating: n/a`. Parser jak w Pythonie: dokładny zestaw kluczy, zamknięty słownik tagów, pewność w [0, 1], usuwanie powtórzonych tagów.

**L2.** Port `extract_graph`:

- parser wybiera ostatni obiekt JSON w odpowiedzi;
- przesunięcia cytatów liczone w punktach kodowych, jak w Pythonie;
- kwarantanna:
  - `hallucination` — cytat nie występuje w tekście;
  - `substring_containment`;
  - `under_minimum_nodes` (< 3);
  - `over_maximum_nodes` (> 7);
- błąd parsowania trafia do `errors`, nie do kwarantanny (tak samo w Pythonie).

**L3 (nowy krok).** Zastępuje enkoder zdań i HDBSCAN:

- węzły `triggered_element` i `lost_value` dostają numery 0..N-1;
- model zwraca `member_idxs` i `representative_idxs`;
- kernel odrzuca indeksy spoza zakresu i powtórzone, a klastry mniejsze niż `min_cluster_size` zamienia w szum;
- wybiera do 5 cytatów reprezentatywnych o różnej treści;
- przy każdym cytacie zapisuje recenzję (`representative_review_ids`).

**L3b.** Port `parse_label_response`: dokładnie klucz `label`, 1–60 znaków, bez prefiksu `UNLABELED:`. Fallback zostawia `UNLABELED:<cluster_id>`.

## AD_Dowody (narzędzie agenta)

| Operacja | Payload | Wynik |
|---|---|---|
| `dowody` | `{cluster, reconciled, heuristic?, utterance_texts?}` | `{entries: [{rank, heuristic, severity, basis: quotes\|artifact_only, reviews: [{review_id, quotes, text}]}], summary}` |

`summary` to gotowy tekst po polsku dla agenta. Ustalenie bez identyfikatorów recenzji ma podstawę `artifact_only`.

## Testy

- `tests/l0_l3.test.ts`: 60 wiadomości L1 i L2 bajt w bajt jak w Pythonie, w tym 41 recenzji z emoji albo znakami `<>&`. Parser L1 i `extract_graph` na prawdziwych odpowiedziach z dziennika odtworzeń: 48 grafów i 12 kwarantann, statusy i powody zgodne z Pythonem. Do tego 14 wiadomości L3b, 40 etykiet, przypadki odrzuceń i grupowanie.
- `tests/dowody.test.ts`: `cluster_11` z Opus 4.7. Sześć ustaleń opartych na cytatach, `upgrade_path_opaque` tylko na artefakcie.
- `tests/symulacja.test.ts`: cały przebieg `cluster_11` przez skrypty z `dist/` w kolejności flow:
  - w miejscu modelu nagrane odpowiedzi Opus 4.6 ze studium;
  - L4, L5, L6 i L7 zgodne z wynikami badania;
  - L8 i weryfikacja zgodne;
  - brief zgodny z briefem badania (poza znacznikiem czasu i tabelą plików).

## Czego nie ma

- Klastrowania przez embeddingi (zob. L3).
- Strumienia głosu biznesu: nie ma go też w kodzie w Pythonie.
