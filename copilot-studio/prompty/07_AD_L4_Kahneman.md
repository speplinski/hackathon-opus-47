# 07 AD_L4_Kahneman

**Warstwa:** L4  
**Model w badaniu:** `claude-sonnet-4-6`  
**Limit odpowiedzi w badaniu:** 6144 tokenów  
**Skrypt kernela:** `AD_Audyt` (wejście: `l4_message`, wyjście: `l4_verdict`)

## Ustawienia w Copilot Studio

1. Tools → Add a tool → New prompt. Nazwa: `AD_L4_Kahneman`.
2. Instructions: wklej całą zawartość `instrukcje/07_AD_L4_Kahneman.txt`.
3. Dla każdego znacznika `{{nazwa}}` usuń go i wstaw w tym miejscu zmienną wejściową o tej samej nazwie (w edytorze wpisz `/`).
4. Model: zgodnie z planem testu (README, sekcja Modele). Temperatura: najniższa dostępna, jeśli ustawienie jest dostępne.
5. Output: Text. JSON wyciąga i sprawdza kernel, więc prompt nie musi mieć zdefiniowanego schematu.

## Wejścia

| Zmienna | Typ | Zawartość |
|---|---|---|
| `wiadomosc` | Text | wynik `l4_message` dla soczewki `audit-decision-psychology` i jednego klastra |
| `zrzut` | Image or document | zrzut ekranu lub eksport makiety z `screenshot_ref` klastra (PNG/JPG/PDF) |

## Uwagi

W siatce badania (wariant multimodalny) każda soczewka dostawała zrzut ekranu przed tekstem wiadomości.

Długość instrukcji: 35 981 znaków.
