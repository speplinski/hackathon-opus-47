# 12 AD_L6_Priorytet

**Warstwa:** L6  
**Model w badaniu:** `claude-opus-4-7`  
**Limit odpowiedzi w badaniu:** 4096 tokenów  
**Skrypt kernela:** `AD_Uzgodnienie` (wejście: `l6_message`, wyjście: `l6_score`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L6_Priorytet`.
2. Instructions: wklej całą zawartość `instrukcje/12_AD_L6_Priorytet.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l6_message` |

## Uwagi

Flow wywołuje ten prompt kilka razy (test stabilności); kernel liczy różnicę między przebiegami.

Długość instrukcji: 15 222 znaków.
