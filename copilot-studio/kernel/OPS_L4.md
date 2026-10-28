# AD_Audyt — operacje warstwy L4 (sześć soczewek)

Office Script `AD_Audyt` (plik `dist/AD_Audyt.ts`, źródła `src/l4.ts`, `src/entry_audyt.ts`). Port deterministycznej części `src/auditable_design/layers/l4_audit*.py`. Model wywołuje flow (akcja *Run a prompt*); skrypt buduje wiadomość wejściową i zamienia odpowiedź modelu na werdykt.

Wywołanie z flow: konektor Excel Online (Business) → **Run script**, parametry `operation` (tekst) i `payload` (tekst JSON). Wynik to `KernelResult` jako tekst JSON: `{ok, operation, result, errors}`.

Soczewki (`lens`) to identyfikatory skilli:

| lens | teoria | artefakty w wiadomości | naprawa JSON |
|---|---|---|---|
| `audit-usability-fundamentals` | Norman | nie (tylko `ui_context`) | nie |
| `audit-accessibility` | WCAG 2.2 | tak | tak |
| `audit-business-alignment` | Osterwalder | tak | tak |
| `audit-decision-psychology` | Kahneman | tak | tak |
| `audit-interaction-design` | Cooper | tak | tak |
| `audit-ux-architecture` | Garrett | tak | tak |

## Operacje

### `l4_message`
- **payload:** `{"lens": "<lens>", "cluster": InsightCluster}`
- **result:** `{"message": "<cluster>…</cluster>"}` — bajt w bajt to samo co `build_user_message` w Pythonie.
- **Krok flow:** przed *Run a prompt* soczewki; `message` idzie do zmiennej wejściowej promptu, a zrzut ekranu (jeśli jest) do wejścia „Image or document”.
- Klaster musi zawierać `representative_review_ids`, jeśli łańcuch dowodowy ma sięgać recenzji (pola nie ma w wiadomości, używa go dopiero `l4_verdict`).

### `l4_verdict`
- **payload:** `{"lens", "cluster", "response_text", "model"?, "produced_at"?, "skill_hash"?, "native_payload_ref"?}`
  - `response_text` — surowy tekst odpowiedzi z *Run a prompt*.
  - `model` — nazwa modelu w Copilot Studio (domyślnie `"unknown"`).
  - `produced_at` — znacznik czasu ISO (domyślnie teraz).
  - `skill_hash` — identyfikator wersji instrukcji promptu (domyślnie `"unknown"`; Office Scripts nie liczą SHA-256).
  - `native_payload_ref` — domyślnie `<plik natywny z Pythona>#<verdict_id>`; podaj własną ścieżkę w OneDrive.
- **result:** `{"status": "audited"|"fallback", "reason", "verdict": AuditVerdict, "native_payload"}`
  - `audited`: `verdict.relevant_heuristics` z dotkliwością Nielsen→0–10 (1→3, 2→5, 3→7, 4→9), `reasoning` w formacie Pythona, `evidence_review_ids` z `evidence_quote_idxs` przez `representative_review_ids`. `native_payload` to sparsowany JSON modelu.
  - `fallback`: `ok: true` (jak w pozostałych skryptach; `ok: false` oznacza tylko błędne wejście), werdykt bez ustaleń, `native_payload = {"fallback": true, "reason", "raw_response"}`. Flow zapisuje go tak samo jak werdykt udany — fallback jest wynikiem (odmowa audytu), nie błędem.
- **Krok flow:** zaraz po *Run a prompt*; flow zapisuje `verdict` do `L4/<lens>.json` i `native_payload` do `L4/<lens>.native.json`.

### `l4_lenses`
- **payload:** `{}`
- **result:** lista soczewek: `lens`, `skill_id`, `layer`, `python_default_model`, `max_tokens`, `native_file`, `artifacts_in_message`. Informacyjnie, np. do pętli „dla każdej soczewki”.

## Testy

`uv run python copilot-studio/kernel/tests/fixtures_l4.py` (z katalogu repo; wymaga `data/derived` i `data/derived_evchain`), potem `bun test tests/l4.test.ts` w `copilot-studio/kernel`. Fixtures obejmują:

- 18 udanych werdyktów ze studium (6 soczewek × `cluster_11`, `cluster_00`, `cluster_12`): wiadomość bajt w bajt i lista `HeuristicViolation` równa Pythonowi;
- wszystkie 11 prawdziwych odrzuceń z siatki: powód odrzucenia identyczny z Pythonem co do znaku;
- 26 przypadków syntetycznych (mutacje poprawnych odpowiedzi): reguły WCAG, napięcia Osterwaldera, `dark_pattern`, `flow_excise`, duplikaty par, zakresy indeksów;
- 14 przypadków `repr()` Pythona (cudzysłowy, znaki niedrukowalne, polskie znaki, emoji).

Wynik: 91/91.

## Czego port nie odtwarza (uczciwie)

1. **Liczby w JSON.** `JSON.parse` nie odróżnia `3` od `3.0`. Python odrzuca `"severity": 3.0` jako float, port go przyjmuje.
2. **Komunikaty błędów składni JSON** pochodzą z silnika JavaScript, nie z modułu `json` Pythona, więc brzmią inaczej. Dla pięciu soczewek z naprawą (`parseModelJson` z `common.ts`) inne są też komunikaty „brak obiektu JSON” i „nie obiekt”.
3. **Naprawa niezacytowanych cudzysłowów** (`_repair_unescaped_string_quotes`) działa tylko wtedy, gdy silnik podaje pozycję błędu w komunikacie. Bun (JavaScriptCore) jej nie podaje. Środowiska Office Scripts nie sprawdziłem, więc naprawa może się tam nie uruchamiać i taka odpowiedź skończy jako fallback.
4. **`NaN`/`Infinity`** — Python `json.loads` je przyjmuje, `JSON.parse` nie.
5. **Wartości nie-skalarne w polach słownikowych** (np. `"dimension": []`): Python rzuca `TypeError` i przerywa warstwę, port zwraca fallback z komunikatem.
6. **Regex WCAG:** `\d` w Pythonie przyjmuje cyfry Unicode, w JavaScript tylko ASCII.
7. **Walidacja Pydantic `AuditVerdict`** (64-znakowy `skill_hash`, typ `datetime` dla `produced_at`) — nieportowana; pola są przekazywane bez sprawdzania.
8. **Poza zakresem skryptu:** wywołanie modelu, dziennik odtworzeń, liczenie `skill_hash`, pliki proweniencji, współbieżność wsadu, CLI. Robi to flow albo nie ma odpowiednika.
9. **Obraz** (tryb multimodalny ze studium) nie jest częścią wiadomości tekstowej; flow przekazuje plik zrzutu jako osobne wejście promptu.
