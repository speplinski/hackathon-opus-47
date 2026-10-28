# 11 AD_L5_Uzgodnienie

**Warstwa:** L5  
**Model w badaniu:** `claude-opus-4-7`  
**Limit odpowiedzi w badaniu:** 16384 tokenów  
**Skrypt kernela:** `AD_Uzgodnienie` (wejście: `l5_message`, wyjście: `l5_reconcile`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L5_Uzgodnienie`.
2. Instructions: wklej całą zawartość `instrukcje/11_AD_L5_Uzgodnienie.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l5_message`: klaster + werdykty sześciu soczewek |

## Uwagi

Ranking, łączenie identyfikatorów recenzji i arytmetykę robi kernel z grafu zwróconego przez model.

Długość instrukcji: 19 840 znaków.
