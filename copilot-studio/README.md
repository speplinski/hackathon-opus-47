# Auditable Design w Copilot Studio

Port potoku Auditable Design na Copilot Studio, Power Automate i OneDrive, do testu w środowisku organizacji. Metoda jest ta sama co w badaniu. Zmienia się tylko miejsce wykonania:

| Część potoku | W Pythonie | Tutaj |
|---|---|---|
| Prompty skilli | SKILL.md jako prompt systemowy | 16 promptów Copilot Studio z identyczną treścią instrukcji (`prompty/`) |
| Kod deterministyczny (budowa wiadomości, walidacja, ranking, łańcuch dowodowy, bramka L8, brief) | moduły `src/auditable_design` | Office Scripts w TypeScript (`kernel/`), sprawdzone testami przeciw Pythonowi |
| Orkiestracja | CLI i skrypty `run_*.sh` | flow `AD_Przebieg` startujący od pliku w OneDrive (`flow/`) |
| Dziennik odtworzeń | `data/cache/responses.jsonl` | plik na każdy krok w `przebiegi/<run_id>/`: wiadomość, odpowiedź, wynik, model, czas |
| Interfejs | brief w markdown | ten sam brief + agent konwersacyjny do uruchamiania i pytań o dowody (`agent/`) |

## Architektura

```
OneDrive /AuditableDesign
  start/run_request.json ──► flow AD_Przebieg (wyzwalacz: nowy plik)
                               │  dla każdego kroku:
                               │   Run script (kernel) → buduje wiadomość
                               │   Run a prompt (model) → odpowiedź
                               │   Run script (kernel) → walidacja, wynik
                               │   Create file → przebiegi/<run_id>/...
                               ▼
  przebiegi/<run_id>/<klaster>/ L4_*.json, L5, L6, L7, L8, weryfikacja, brief.md

Agent „Auditable Design” (Copilot Studio)
  ├─ AD_Uruchom → tworzy run_request.json (odpowiada w kilka sekund)
  ├─ AD_Status  → czyta status.json
  └─ AD_Dowody  → kernel AD_Dowody: recenzje i cytaty za ustaleniem albo „tylko artefakt”
```

Dlaczego przebieg prowadzi flow, a nie generatywna orkiestracja agentów:

- **Powtarzalność.** Metoda wymaga tej samej kolejności kroków i zapisu każdego z nich. Generatywny orkiestrator sam wybiera narzędzia.
- **Czas.** Flow wywoływany przez agenta musi odpowiedzieć w 100 s, synchronicznie. Przebieg trwa dłużej.

Źródła: [orkiestracja](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-generative-actions), [flow jako narzędzie](https://learn.microsoft.com/en-us/microsoft-copilot-studio/flow-agent).

## Zawartość

| Folder | Co zawiera |
|---|---|
| `prompty/` | 16 promptów: dokumentacja (`.md`) i tekst do wklejenia (`instrukcje/*.txt`); generowane przez `narzedzia/generuj_prompty.py` |
| `kernel/` | źródła TypeScript (`src/`), testy zgodności z Pythonem (`tests/`), gotowe skrypty do wklejenia (`dist/`), opis operacji (`OPS_*.md`) |
| `flow/` | `F0_przygotowanie.md` (jednorazowa konfiguracja), `F1_AD_Przebieg.md` (główny flow krok po kroku) |
| `agent/` | `AD_Agent.md`: instrukcje agenta i jego trzy narzędzia |
| `narzedzia/` | generator promptów, budowa folderu OneDrive z danymi testowymi |

## Kolejność budowy

1. **Przygotowanie** wg `flow/F0_przygotowanie.md`: OneDrive, `AD_Kernel.xlsx`, sześć skryptów, 16 promptów.
2. **Próba kernela:** `AD_Audyt` / `l4_lenses`.
3. **Flow `AD_Przebieg`, tryb A**, dla jednego klastra (`cluster_11`) i jednego modelu.
4. **Porównanie** z `wejscie/badanie_A/referencja/<model>/`.
5. **Agent** i jego narzędzia.
6. **Tryb B** na 60 wypowiedziach, potem na całym korpusie.

## Plan testu

**Test A: od klastrów ze studium.** Wejście to sześć klastrów, które w badaniu trafiły do audytu L4, ze zrzutami ekranu i identyfikatorami recenzji. Wiadomości budowane przez kernel są bajt w bajt takie same jak w badaniu (testy w `kernel/tests`). Różnica w wynikach pochodzi więc tylko z modelu i platformy, a nie z przeniesienia kodu. Copilot Studio oferuje Claude Opus 4.6 i Sonnet 4.6 ([lista modeli](https://learn.microsoft.com/en-us/microsoft-copilot-studio/prompt-model-settings)). To dwa z trzech modeli badania, więc wyniki porównasz bezpośrednio z `referencja/opus46` i `referencja/sonnet46`.

Co porównać dla `cluster_11`:

- liczbę ustaleń i odmów L4 na soczewkę;
- ranking L5;
- udział ustaleń z cytatem;
- sumę dotkliwości przed pętlą L8 i po niej;
- werdykty weryfikacji na zrzutach.

**Test B: od wypowiedzi.** Pełny potok na korpusie 600 recenzji. Krok L3 jest tu inny niż w badaniu (opis niżej), więc klastry nie pokryją się 1:1 ze studium. Ten test sprawdza, czy cały łańcuch działa od surowego tekstu, a nie odtwarzalność wyników.

**Potem dane organizacji.** Zamień wejście na wypowiedzi z badań (L0 przyjmuje dowolny typ źródła), zrzuty ekranu na eksporty makiet, a modele na dopuszczone polityką.

## Modele

Model wybiera się osobno w każdym prompcie. Na jeden przebieg ustaw ten sam model we wszystkich promptach i wpisz go w `run_request.json` (`model_opis`), żeby trafił do zapisu każdego kroku.

| Etap | Do testu A | Do danych organizacji |
|---|---|---|
| wszystkie prompty | Claude Opus 4.6, potem Claude Sonnet 4.6 | modele dopuszczone polityką (np. z rodziny GPT dostępnej w środowisku) |

Microsoft opisuje modele Anthropic w Copilot Studio jako eksperymentalne, nieprzeznaczone do produkcji i hostowane poza Microsoft. Wymagają włączenia przez administratora ([Learn](https://learn.microsoft.com/en-us/microsoft-copilot-studio/authoring-select-external-response-model)). Do testu na danych publicznych to wystarcza. Do danych klientów już nie.

## Odstępstwa od badania (do opisania w wynikach)

1. **L3 bez enkodera i HDBSCAN.** Copilot Studio nie uruchomi lokalnego modelu embeddingów, więc klastry tworzy prompt `AD_L3_Grupowanie`. Kernel sprawdza każdy zwrócony indeks, odrzuca klastry mniejsze niż 5 i zapisuje recenzję przy każdym cytacie reprezentatywnym. Wybiera też cytaty o różnej treści, bo powtórzone pojedyncze słowa („energy” ×5) nie dają się przypisać do jednej recenzji. Test A omija ten krok.
2. **Brak oddzielnego promptu systemowego.** Prompt Copilot Studio ma jedno pole instrukcji. Treść skilla stoi na początku, a wejście w sekcji INPUT na końcu.
3. **Temperatura.** W badaniu 0, z wyjątkiem Opus 4.7. W Copilot Studio ustaw najniższą dostępną, jeśli prompt ma takie ustawienie.
4. **Dziennik.** Zamiast dziennika odtworzeń z kluczem SHA-256 każdy krok zapisuje wiadomość, odpowiedź i wynik. Przebiegu nie da się odtworzyć bez modelu, ale da się go w całości przejrzeć.
5. **Drobne różnice parsowania** (np. `3.0` przyjmowane jako liczba całkowita) są opisane w `kernel/OPS_*.md`.

## Testy kernela

182 testy, wszystkie oparte na danych ze studium i wartościach wyliczonych przez kod w Pythonie:

- **Wiadomości do modeli:** wszystkie warstwy bajt w bajt jak w badaniu.
- **Parsery:** na prawdziwych odpowiedziach z dziennika i ze studium, plus przypadki odrzuceń.
- **Wyniki:** ranking L5 z identyfikatorami recenzji, priorytet L6, decyzja L7, iteracje i warunki stopu pętli L8, artefakty weryfikacji i brief.
- **Symulacja przebiegu** (`tests/symulacja.test.ts`): skrypty z `dist/`, czyli dokładnie te wklejane do Excela, wołane w kolejności flow z nagranymi odpowiedziami Opus 4.6. Brief wychodzi identyczny z briefem badania. Ten test złapał dwa błędy na stykach skryptów: kolejność kluczy priorytetu i brak obiektu rodzica w planie pętli.

Opis operacji i tego, czego port nie odtwarza: `kernel/OPS_L0_L3.md`, `OPS_L4.md`, `OPS_L5_L7.md`, `OPS_L8_BRIEF.md`.

Uruchomienie (fixtures są w repozytorium, dane studium potrzebne tylko do ich odtworzenia):

```
cd copilot-studio/kernel && bun test tests && bun build.ts
# odtworzenie fixtures, z katalogu repozytorium:
uv run python copilot-studio/kernel/tests/fixtures_l0_l3.py   # oraz fixtures_l4, _l5_l7, _l8_brief, _symulacja
```

Czego testy nie sprawdzają: zachowania samego Copilot Studio i Power Automate. Nie wiadomo, czy edytor przyjmie instrukcje o długości 24–37 tys. znaków, jak *Run a prompt* przekazuje obrazy, jakie biblioteki ma interpreter kodu i czy środowisko Office Scripts podaje pozycję błędu JSON, której potrzebuje naprawa cudzysłowów. To sprawdzi dopiero pierwszy przebieg (F0, punkt 5, i test A).
