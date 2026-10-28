# F1. Flow `AD_Przebieg` (cały potok)

Flow prowadzi przebieg deterministycznie: zawsze te same kroki w tej samej kolejności. Każdy krok zapisuje w folderze przebiegu, co wysłał do modelu, co dostał i co z tego wyliczył kernel. Ten zapis zastępuje dziennik odtworzeń z wersji w Pythonie.

## Wzorce używane w każdym kroku

**W1. Wywołanie kernela.** Excel Online (Business) → **Run script**

- Location: OneDrive for Business; Document Library: OneDrive.
- File: `/AuditableDesign/kernel/AD_Kernel.xlsx`.
- Script: np. `AD_Audyt`.
- `operation`: nazwa operacji.
- `payload`: tekst JSON.

Payload buduj akcją **Compose** z obiektem JSON i dynamiczną zawartością, a do skryptu przekazuj `string(outputs('Compose_…'))`.

Wynik odczytujesz wyrażeniem:

```
json(outputs('Run_script_…')?['body/result'])?['result']
```

Po każdym wywołaniu dodaj **Condition**: `json(outputs('Run_script_…')?['body/result'])?['ok']` równe `true`. Gałąź „nie” zapisuje błąd w `status.json` i kończy przebieg akcją **Terminate** (Failed). `ok:false` oznacza błędne wejście. Odpowiedź modelu nie do użycia wraca jako `ok:true` ze statusem `fallback` i jest zwykłym wynikiem do zapisania.

**W2. Wywołanie modelu.** AI capabilities → **Run a prompt**

- Prompt: np. `AD_L4_Cooper`.
- Wejście `wiadomosc`: wiadomość z kernela.
- Wejście obrazu (jeśli jest): zawartość pliku z OneDrive (akcja **Get file content using path**).

Tekst odpowiedzi wybierz z dynamicznej zawartości akcji (pole *Text*). Nie parsuj go w flow, tylko przekaż do kernela.

**W3. Zapis kroku.** OneDrive → **Create file**

- Folder: `/AuditableDesign/przebiegi/@{run_id}/@{cluster_id}`.
- Nazwa: np. `L4_audit-interaction-design.json`.
- Treść: `string(outputs('Compose_zapis'))`, gdzie **Compose** zawiera obiekt `{"wiadomosc": <wiadomość>, "odpowiedz": <tekst odpowiedzi>, "wynik": <result z kernela>, "model": <model_opis>, "czas": utcNow()}`.

Nie skleja się JSON-a funkcją `concat`: tekst odpowiedzi modelu zawiera cudzysłowy i znaki nowej linii, więc wynik nie byłby poprawnym JSON-em. Compose z obiektem escapuje je sam.

**W4. Stan.** Po każdym etapie nadpisz `/AuditableDesign/przebiegi/@{run_id}/status.json` obiektem `{etap, klaster, bledy, zakonczony}`. Czyta go narzędzie agenta `AD_Status`.

**Limity.** Zapytania generatywne mają limit 50 na minutę przy 1–10 pakietach ([Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/requirements-quotas)). W pętlach **Apply to each** z promptem ustaw *Concurrency control* na 5.

## Wyzwalacz i start

1. **Wyzwalacz:** OneDrive for Business → *When a file is created*, folder `/AuditableDesign/start`. Pliki powyżej 50 MB są pomijane, a run_request ma kilka KB.
2. **Parse JSON** z treści pliku: `run_id`, `tryb`, `zestaw`, `klastry`, `klastry_l5` (opcjonalne), `limit_wypowiedzi`, `model_opis`.
3. **Initialize variable** dla: `run_id`, `model_opis`, `folder = /AuditableDesign/przebiegi/@{run_id}`, `wejscie = /AuditableDesign/wejscie/@{zestaw}`.
4. Get file content `/AuditableDesign/konfiguracja/konfiguracja.json` → Parse JSON (`soczewki`, `l3_min_rozmiar_klastra`, `l6_liczba_przebiegow`, `l8_max_iteracji`).
5. Create file `@{folder}/run_request.json` (kopia pliku startowego) i `status.json` (`etap: start`).

## Gałąź trybu (Switch na `tryb`)

### A_od_klastrow — start od klastrów ze studium

1. Get file content `@{wejscie}/klastry.json`.
2. **Filter array** po `cluster_id` należących do `klastry` z run_request (puste = wszystkie).
3. Wynik to tablica `klastry_do_audytu`. Przejdź do etapu „Audyt i decyzja”.

### B_od_wypowiedzi — pełny potok od wypowiedzi

Skrypt: `AD_Wejscie`. Etapy zapisuj w `@{folder}/wejscie/`.

1. **L0.** Get file content `@{wejscie}/wypowiedzi.json` → `l0_normalize` z payloadem `{"sources": …}` → zapisz `L0_wypowiedzi.json`. Jeśli jest `limit_wypowiedzi`, weź pierwsze N (`take(...)`). Pierwszy test rób na 60, nie na 600.
2. **L1.**
   1. `l1_messages` `{utterances}`.
   2. Apply to each wiadomości (concurrency 5): Run a prompt `AD_L1_Klasyfikacja`, potem **Append to array variable** `l1_odp` z `{utterance_id, response_text}`.
   3. `l1_parse` `{items: l1_odp}`.
   4. Zapisz `L1.json`.
   5. Filter array: `is_ux_relevant` = true.
3. **L2.**
   1. `l2_messages` dla wypowiedzi z L1.
   2. Apply to each: Run a prompt `AD_L2_GrafSkargi`, Append `{utterance, response_text}`.
   3. `l2_extract` `{items}`.
   4. Zapisz `L2_grafy.json`, `L2_kwarantanna.json` i `L2_bledy.json`.
4. **L3.**
   1. `l3_message` `{graphs}`.
   2. Run a prompt `AD_L3_Grupowanie`.
   3. `l3_clusters` `{nodes, response_text, min_cluster_size}`.
   4. Zapisz `L3_klastry.json`.
5. **L3b.** Apply to each klaster:
   1. `l3b_message`.
   2. Run a prompt `AD_L3b_Etykieta`.
   3. `l3b_label`.
   4. Zbierz klastry z etykietami w `klastry_do_audytu` i zapisz `L3b_klastry.json`.

W trybie B klastry nie mają `ui_context` ani `screenshot_ref`. Przed audytem możesz je uzupełnić ręcznie w `L3b_klastry.json` (opis ekranu i nazwa pliku w `artefakty/`) i uruchomić tryb A na tym pliku. To odpowiada krokowi, który w studium robił skrypt `build_l4_inputs.py`.

## Audyt i decyzja (Apply to each `klastry_do_audytu`, sekwencyjnie)

Folder klastra: `@{folder}/@{cluster_id}`.

Na początku ustal dwa obiekty klastra:

- `klaster` — bieżący element pętli; idzie do L4.
- `klaster_l5` — idzie do L5–L8, weryfikacji i briefu. Jeśli `run_request` ma pole `klastry_l5`: Get file content `@{wejscie}/@{klastry_l5}` → Filter array po `cluster_id` → `first(...)`. W przeciwnym razie `klaster_l5 = klaster`.

Pole `klastry_l5` służy wierności testu A. W badaniu L4 widziało klaster z opisem ekranu i etykietą Opus 4.7, a L5–L8 klaster bez opisu ekranu z etykietą modelu danej gałęzi. Bez tego pola wiadomości L5–L8 różniłyby się od badania.

Zapisz `klaster_l5` jako `klaster.json`. Tego pliku używa narzędzie agenta `AD_Dowody`.

### L4: sześć soczewek (skrypt `AD_Audyt`)

Apply to each `soczewki` z konfiguracji (concurrency 1–3):

1. `l4_message` `{lens, cluster}`.
2. Jeśli klaster ma `screenshot_ref`: Get file content `@{wejscie}/artefakty/@{last(split(screenshot_ref,'/'))}`. W siatce badania obraz dostawała każda soczewka, także Norman (jego wiadomość tekstowa nie ma znacznika `screenshot_ref`, ale obraz był dołączony).
3. **Switch** na `lens`, w każdej gałęzi Run a prompt właściwej soczewki:

   | lens | prompt |
   |---|---|
   | `audit-usability-fundamentals` | `AD_L4_Norman` |
   | `audit-accessibility` | `AD_L4_WCAG` |
   | `audit-decision-psychology` | `AD_L4_Kahneman` |
   | `audit-business-alignment` | `AD_L4_Osterwalder` |
   | `audit-interaction-design` | `AD_L4_Cooper` |
   | `audit-ux-architecture` | `AD_L4_Garrett` |

   Wejście `wiadomosc` = `message`; wejście `zrzut` = plik z kroku 2.
4. `l4_verdict` `{lens, cluster, response_text, model: model_opis, produced_at: utcNow()}`.
5. Append `verdict` do zmiennej `werdykty_l4`.
6. Zapisz `L4_<lens>.json` (wzorzec W3) i `L4_<lens>.native.json`.

Status `fallback` (odmowa albo odpowiedź nie do użycia) zapisujesz tak samo jak udany werdykt. W studium odmowy były osobną miarą.

### L5–L7 (skrypt `AD_Uzgodnienie`)

1. **L5.**
   1. `l5_message` `{cluster: klaster_l5, verdicts: werdykty_l4}`.
   2. Jeśli `skip`: `l5_reconcile` z `response_text: null`.
   3. W przeciwnym razie Run a prompt `AD_L5_Uzgodnienie`, potem `l5_reconcile` `{cluster: klaster_l5, verdicts, response_text}`.
   4. Zapisz `L5_uzgodnienie.json` (`verdict`) i `L5_uzgodnienie.native.json` (`native_row`).
2. **L6.**
   1. `l6_message` `{cluster: klaster_l5, reconciled}`.
   2. Run a prompt `AD_L6_Priorytet` dwa razy z tą samą wiadomością.
   3. `l6_score` `{cluster_id, response_texts: [odp1, odp2]}`.
   4. Jeśli `status` = `needs_third_pass`: trzecie wywołanie i `l6_score` z trzema odpowiedziami.
   5. Zapisz `L6_priorytet.json`.
3. **L7.**
   1. `l7_message` `{cluster: klaster_l5, reconciled, priority}`.
   2. Run a prompt `AD_L7_Decyzja`.
   3. `l7_decide` `{cluster: klaster_l5, reconciled, response_text}`.
   4. Zapisz `L7_decyzja.json` (`principle`, `decision`, `native_row`).

### L8: pętla optymalizacji (skrypt `AD_Petla`)

Folder artefaktów iteracji: `artifacts_dir = @{folder}/@{cluster_id}/iteracje`.

1. **Iteracja 0 i 1.**
   1. `l8_baseline` `{cluster: klaster_l5, reconciled, decision, run_id, artifacts_dir}`.
   2. Zapisz `artifact_md` pod `artifact_ref`.
   3. Run a prompt `AD_L8_Ocena` z `reaudit_message`.
   4. `l8_proposed` `{cluster: klaster_l5, reconciled, decision, baseline_iteration: iteration, response_text, run_id, artifacts_dir}`.
   5. Zapisz `artifact_md` i `iteration`.
   6. Zmienna `iteracje` = `[iteracja0, iteracja1]`, zmienna `kroki` = `[]`, zmienna `artefakty` = `{}`.
2. **Pętla:** **Do until** `plan.action` = `stop`, limit 10 obrotów.
   1. `l8_loop_plan` `{cluster: klaster_l5, reconciled, decision, existing_iterations: [iteracja0, iteracja1], steps: kroki, max_iterations: l8_max_iteracji}`. Jeśli wcześniejszy krok zwrócił `fallback`, dodaj `halt_reason`.
   2. Jeśli `action` = `stop`: zapisz `termination_reason` i wyjdź z pętli.
   3. Run a prompt `AD_L8_Poprawka` z `tweak_message`.
   4. `l8_tweak_parse` `{response_text, current_scores: parent_scores, cluster: klaster_l5, reconciled, parent_snapshot}`. Przy `fallback` ustaw `halt_reason = termination_reason` i wróć do kroku 1.
   5. Run a prompt `AD_L8_Ocena` z `reaudit_message` z poprzedniego kroku.
   6. `l8_loop_step` `{cluster: klaster_l5, reconciled, parent_iteration, parent_snapshot, next_index, tweak_response_text, reaudit_response_text, verifier: l8_weryfikator, run_id, artifacts_dir}`. Przy `fallback`: `halt_reason` jak wyżej.
   7. Zapisz `artifact_md` pod `artifact_ref`.
   8. Dopisz do `artefakty` parę `artifact_ref` → `artifact_md`.
   9. Append do `kroki`: `{iteration, new_snapshot, converged: verdict.converged}`; append `iteration` do `iteracje`.
3. Zapisz `L8_iteracje.json` (`iteracje`, `termination_reason`).

Bramkę akceptacji (Tchebycheff albo Pareto) i warunki stopu liczy kernel. Model tylko proponuje poprawkę i ocenia projekt. Odrzucone iteracje zostają w zapisie, tak jak w badaniu.

### Weryfikacja na produkcie (skrypt `AD_Petla`)

1. Get file content `@{wejscie}/artefakty/opisy.json`, potem Parse JSON: lista `{plik, path, label}` w kolejności podawania obrazów.
2. `vp_message` `{reconciled, screenshots: [{label, path}]}`.
3. Get file content dla każdego `plik` (do trzech).
4. Run a prompt `AD_Weryfikacja`: `zrzut_1..3` = pliki, `wiadomosc` = `prompt_text`.
5. `vp_parse` `{response_text, reconciled}`. Zapisz `weryfikacja.json` (`payload`).
6. Opcjonalnie: `vp_render` `{reconciled, payload, model: model_opis, screenshots}` → `weryfikacja.md`.

W organizacji zamiast zrzutów ekranu podajesz eksporty makiet (PNG albo PDF) z opisami w `opisy.json`.

### Brief (skrypt `AD_Brief`)

1. `brief_render` `{cluster: klaster_l5, reconciled, priority, decision, iterations: iteracje, verify_payload, model_short, loop_verifier, artifact_bodies: artefakty, inputs: [{label, path}]}`.
   - `model_short`: skrót z `model_opis`, np. `opus46`.
   - `inputs`: ścieżki plików przebiegu do tabeli proweniencji. Skróty SHA-256 zostaną puste, bo Office Scripts ich nie liczą.
2. Create file `@{folder}/@{cluster_id}/brief.md` z `markdown`.
3. Opcjonalnie skopiuj brief do `@{folder}/briefy/`.

Brief zawiera sekcję „Evidence per finding”: dla każdego ustalenia identyfikatory recenzji i cytaty albo oznaczenie, że opiera się tylko na artefakcie.

## Zakończenie

1. Zapisz `status.json` z `zakonczony: true`.
2. Opcjonalnie Outlook/Teams: wiadomość z linkiem do folderu przebiegu i briefów.
