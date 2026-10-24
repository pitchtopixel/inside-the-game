# Groundedness report

Every saved answer (`data/cache/answers.json`) judged by the Foundry **Groundedness evaluator**
(azure-ai-evaluation, judge `gpt-4.1-mini`) against what its agent was given. Score 1-5; pass = 4+.
Personalizer rewrites are judged against the overlay they rewrote, so translations are checked for meaning.

**321/329 answers grounded (98%)** · average score 4.82 · judge errors 0

| Agent | Task | Language | Answers | Grounded | Avg score |
|---|---|---|---|---|---|
| explainer | explain | en | 50 | 47 | 4.82 |
| narrator | commentary | en | 100 | 96 | 4.88 |
| narrator | recap | en | 8 | 8 | 5.00 |
| personalizer | personalize (broadcaster) | en | 54 | 54 | 5.00 |
| personalizer | personalize (fan) | en | 53 | 53 | 4.68 |
| personalizer | personalize (fan) | es | 32 | 32 | 4.69 |
| personalizer | personalize (fan) | hi | 32 | 31 | 4.62 |

## Below 4 (8)

- **2.0** · narrator / commentary / en: 27:15 yellow card for northgate's brandt disrupts their momentum despite leading in threat against riverside's 2-0 advantage.
  - judge: Let's think step by step: The response states that at 27:15, a yellow card was given to Northgate's Brandt, which disrupted their momentum despite leading in threat against Riverside's 2-0 advantage. From the context, the yellow card for Brandt (Northgate) at 27:15 is correct. However, the context shows that Riverside is leading 2-0, and Northgate is actually on top in threat share (98.8%), but th
- **2.0** · explainer / explain / en: Harbourside asserts control with long possession | With the score tied, Harbourside's 12-pass possession streak imposes rhythm and pressure on Eastvale, shifting momentum away from their higher threat share. | control
  - judge: Let's think step by step: The response headline "Harbourside asserts control with long possession" directly relates to the context fact that Harbourside had a 12-pass possession streak. The explanation correctly notes the tied score (1-1) and the possession streak's impact on rhythm and pressure, which aligns with the context's mention of possession share and momentum. However, the response incorr
- **2.0** · explainer / explain / en: Yellow card raises Eastvale's challenge | Castelnau's booking heightens pressure on Eastvale trailing 2-1 with momentum heavily against them. Harbourside’s dominance in possession and chances means this discipline moment could shift control further. | discipline
  - judge: Let's think step by step: The response headline states 'Yellow card raises Eastvale's challenge,' which is consistent with the context showing Castelnau of Eastvale received a yellow card. However, the response incorrectly states that Eastvale is trailing 2-1, while the context score line is 'Harbourside 1-2 Eastvale,' meaning Eastvale is actually leading. The response also claims Harbourside has 
- **2.0** · explainer / explain / en: Eastvale dominates possession and rhythm | Despite trailing, Eastvale controls 78% of possession, dictating play and applying intense pressure with 100 presses. This control phase shifts momentum and increases their chance to recover the scoreline. | control
  - judge: Let's think step by step: The response headline states that Eastvale dominates possession and rhythm, which aligns with the context showing Eastvale having 77.8% possession share and being in a control state. The response mentions Eastvale controlling 78% of possession and applying intense pressure with 100 presses, both supported by the context data. However, the response incorrectly states that 
- **2.0** · narrator / commentary / en: 71:48 Eastvale pressing hard with 78% possession, controlling the game but still trailing 2-1 to Harbourside.
  - judge: Let's think step by step: The response states that at 71:48, Eastvale is pressing hard with 78% possession, controlling the game but still trailing 2-1 to Harbourside. According to the context, Eastvale has 77.8% possession (rounded to 78%), is applying 100 pressures (indicating intense pressing), and is trailing with a score line of Harbourside 1-2 Eastvale. However, the context shows Eastvale is
- **2.0** · narrator / commentary / en: 51:15 yellow card to castelnau raises pressure on Eastvale as harbourside commands 58% possession and 91% threat share.
  - judge: Let's think step by step: The response accurately mentions the yellow card to Castelnau at 51:15, which is a key disciplinary moment in the context. It also correctly states that this raises pressure on Eastvale, aligning with the context's explanation that the booking heightens pressure on Eastvale. The response includes Harbourside's possession at 58%, which matches the context's possession perc
- **2.0** · narrator / commentary / en: 51:15 - yellow card for Castelnau adds pressure on Eastvale as Harbourside control possession and attack with 91% threat share.
  - judge: Let's think step by step: The response accurately mentions the yellow card for Castelnau at 51:15, which is a key disciplinary moment in the context. It also correctly states that this adds pressure on Eastvale, which aligns with the context's explanation that the booking heightens pressure on Eastvale. However, the response incorrectly states that Harbourside controls possession and attack with 9
- **2.0** · personalizer / personalize (fan) / hi: Bittencourt के दो गोल से पलड़ा Harbourside के पक्ष में | 45+2:06 - Bittencourt का दूसरा गोल Harbourside को 2-1 से आगे ले गया, पलड़ा पूरी तरह बदल गया! | Bittencourt का यह गोल Harbourside के लिए बड़ा मौका है, जो कम कब्जे के बावजूद बढ़त दिलाता है।
  - judge: Let's think step by step: The response accurately reflects the key points from the context. It mentions Bittencourt's second goal at 45+2:06, which puts the team (referred to as Harbourside in the response instead of Eastvale) ahead 2-1, indicating a shift in momentum. It also notes the significance of the goal despite lower possession, consistent with the context. However, the response changes th
