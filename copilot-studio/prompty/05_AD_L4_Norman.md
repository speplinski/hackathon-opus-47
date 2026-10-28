# 05 AD_L4_Norman

**Warstwa:** L4  
**Model w badaniu:** `claude-sonnet-4-6`  
**Limit odpowiedzi w badaniu:** 4096 tokenów  
**Skrypt kernela:** `AD_Audyt` (wejście: `l4_message`, wyjście: `l4_verdict`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L4_Norman`.
2. Instructions: wklej całą zawartość `instrukcje/05_AD_L4_Norman.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l4_message` dla soczewki `audit-usability-fundamentals` i jednego klastra |
| `zrzut` | Image or document | zrzut ekranu lub eksport makiety z `screenshot_ref` klastra (PNG/JPG/PDF) |

## Uwagi

W siatce badania (wariant multimodalny) każda soczewka dostawała zrzut ekranu przed tekstem wiadomości. Wiadomość Normana nie zawiera znaczników html i screenshot_ref (tak buduje ją Python), ale obraz był dołączany.

Długość instrukcji: 22 880 znaków.
