# 13 AD_L7_Decyzja

**Warstwa:** L7  
**Model w badaniu:** `claude-opus-4-7`  
**Limit odpowiedzi w badaniu:** 4096 tokenów  
**Skrypt kernela:** `AD_Uzgodnienie` (wejście: `l7_message`, wyjście: `l7_decide`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L7_Decyzja`.
2. Instructions: wklej całą zawartość `instrukcje/13_AD_L7_Decyzja.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l7_message` |

Długość instrukcji: 13 301 znaków.
