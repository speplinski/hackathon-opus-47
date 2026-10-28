# 15 AD_L8_Poprawka

**Warstwa:** L8  
**Model w badaniu:** `claude-opus-4-7`  
**Limit odpowiedzi w badaniu:** 4096 tokenów  
**Skrypt kernela:** `AD_Petla` (wejście: `l8_loop_plan`, wyjście: `l8_tweak_parse`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L8_Poprawka`.
2. Instructions: wklej całą zawartość `instrukcje/15_AD_L8_Poprawka.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | `tweak_message` z `l8_loop_plan` |

Długość instrukcji: 10 665 znaków.
