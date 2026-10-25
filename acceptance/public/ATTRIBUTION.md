# Openly licensed public clips

Fetched 2026-10-23 from the Wikimedia Commons API (`prop=imageinfo`,
`iiprop=url|extmetadata|size|sha1|mime`). The raw response is `commons.json`;
`site/build_data.py` reads attribution from it. SHA-1 of each download matches
the `sha1` Commons reports.

Both clips were checked frame by frame at one frame per second: rooftops, a blast
cloud and ruined buildings, no identifiable person.

## jdeideh_2015.webm

- Commons file: File:Jdeideh Jdayde Aleppo Avril 2015.webm
- Source: https://commons.wikimedia.org/wiki/File:Jdeideh_Jdayde_Aleppo_Avril_2015.webm
- Author (Artist field): Jdeideh Jdayde, https://www.youtube.com/channel/UCP1lupeS4ZwPSAnR5izmf7A
- Credit: YouTube, https://www.youtube.com/watch?v=f4tk4lDMFj0
- Licence: CC BY 3.0, https://creativecommons.org/licenses/by/3.0
- Date (DateTimeOriginal): 2015-04-30
- SHA-1: 0d360c1270569f16338b6e914807bef1186b580c
- Note: the Commons description text says "April 2016"; the file title, the
  burned-in caption and DateTimeOriginal say April 2015. The frames carry a
  burned-in caption and an armed group's logo from the original upload.

## jdeideh_2017.webm

- Commons file: File:Jdeideh Alep - Waqf complexes of Ibshir Pasha and Bahram Pasha - Sept 2017.webm
- Source: https://commons.wikimedia.org/wiki/File:Jdeideh_Alep_-_Waqf_complexes_of_Ibshir_Pasha_and_Bahram_Pasha_-_Sept_2017.webm
- Author: Ortho2017 (own work)
- Licence: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0
- Date (DateTimeOriginal): 2017-09-13
- SHA-1: 1a54147c4f628ef52ffec80a3e7b1f030849ade7

# Openly licensed photographs

Fetched 2026-10-24 from the Wikimedia Commons API (`prop=imageinfo`,
`iiprop=url|extmetadata|size|sha1|mime`); the responses are merged into
`commons.json`. SHA-1 of each download matches the `sha1` Commons reports.
Shown unaltered (the case photo is downscaled). The duplicates chapter, the search chapter and the arrive chapter of the site use them;
they are not part of the synthetic case, and the site captions repeat only what the
Commons page says.

## aleppo_building.jpg

- Commons file: File:Immeuble bombardé à Alep.jpg
- Source: https://commons.wikimedia.org/wiki/File:Immeuble_bombard%C3%A9_%C3%A0_Alep.jpg
- Author: Cagibi54 (own work)
- Licence: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0
- Date (DateTimeOriginal): 2019-10-06 16:15:28
- Commons description: "Après les bombardements russes"
- SHA-1: 308eade72d1c2dbe8413fd2e0eefb7ad6a2b00af
- Use: the source picture of the duplicates chapter. No people.

## aleppo_souk.jpg

- Commons file: File:Le souk d'Alep bombardé.jpg
- Source: https://commons.wikimedia.org/wiki/File:Le_souk_d%27Alep_bombard%C3%A9.jpg
- Author: Cagibi54 (own work)
- Licence: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0
- Date (DateTimeOriginal): 2019-10-06 16:52:04
- Commons description: "Dans les ruines du souk d'Alep"
- SHA-1: 01334ac0423ec4826682a212236c2286b43549ec
- Use: text-to-image retrieval. Three visitors walk in the lane; one face is already
  blurred in the upload.

## aleppo_checkpoint.png

- Commons file: File:A Syrian Arab Army checkpoint abandoned due to a massive rebel offensive, December 2024.png
- Source: https://commons.wikimedia.org/wiki/File:A_Syrian_Arab_Army_checkpoint_abandoned_due_to_a_massive_rebel_offensive,_December_2024.png
- Author: Unknown author (Credit field: https://www.voanews.com/a/syria-s-civil-war-intensifies-as-rebels-take-major-city-in-lightning-offensive-/7884273.html)
- Licence: Public domain (template PD-VOA on the file page; Commons gives no licence URL)
- Date: 2024-12-02
- Commons description: "A Syrian Arab Army checkpoint abandoned due to a massive rebel offensive and capturing of Aleppo, December 2024. Damaged and torn portraits of Hafez and Bashar al-Assad are visible"
- SHA-1: 50f557b861b344ee3c73b7a18ebe6cc9cab50242
- Use: text-to-image retrieval. A video still with a VOA logo; figures in the
  background are a few pixels tall, no armed-group emblem.

## homs_bab_dreeb_1.jpg

- Commons file: File:Destruction in Homs (8).jpg
- Source: https://commons.wikimedia.org/wiki/File:Destruction_in_Homs_(8).jpg
- Author: Bo yaser (own work)
- Licence: CC BY-SA 3.0, https://creativecommons.org/licenses/by-sa/3.0
- Date (DateTimeOriginal): 2012-04-05
- Commons description: "Destruction in the Bab Dreeb area of Homs, Syria (2012)."
- SHA-1: 6feb240d7f549645381c7dc4b34fd7b941a1e982
- Use: source of `acceptance/data/photo_damaged_building.jpg` (downscaled to
  1024x768, signed with the c2patool test certificate). The synthetic case makes
  no claim about this building. No people.

## homs_bab_dreeb_2.jpg

- Commons file: File:Destruction in Homs (9).jpg
- Source: https://commons.wikimedia.org/wiki/File:Destruction_in_Homs_(9).jpg
- Author: Bo yaser (own work)
- Licence: CC BY-SA 3.0, https://creativecommons.org/licenses/by-sa/3.0
- Date (DateTimeOriginal): 2012-04-05
- Commons description: "Destruction in the Bab Dreeb area of Homs, Syria (2012)."
- SHA-1: 5d1e1b1ca35bc2cd76a05ea73899d068ef29f7e8
- Use: extra photo in the app demo seed (`sealbook/monitor/seed_full.py`). No people.

## raqqa_destroyed_2017.png

- Commons file: File:Destroyed neighborhood in Raqqa.png
- Source: https://commons.wikimedia.org/wiki/File:Destroyed_neighborhood_in_Raqqa.png
- Author: Mahmoud Bali (VOA)
- Licence: Public domain (template PD-VOA; Commons gives no licence URL)
- Date (DateTimeOriginal): 2017-08-01
- Commons description: "A destroyed part of Raqqa (Syria), during the 2017 military offensive launched against the Islamic State."
- SHA-1: b6d06d68c0b6357625b8af6f3b9daa1b1be60bf2
- Use: extra photo in the app demo seed. Wide street view; no person identifiable.

# More openly licensed photographs for the extra demo cases

Fetched 2026-10-24 from the Wikimedia Commons API (`prop=imageinfo`, `iiprop=url|extmetadata|size|sha1|mime`); merged into `commons.json`. SHA-1 of each download matches the `sha1` Commons reports. Shown unaltered. Licences were read from the file pages. Used only as photo sources in `stk demo-seed --more`; no claim in any demo case is tied to them. Frames checked: no people in any of them.

## homs_bab_dreeb_3.jpg

- Commons file: File:Destruction in Homs (1).jpg
- Source: https://commons.wikimedia.org/wiki/File:Destruction_in_Homs_(1).jpg
- Author: Bo yaser (own work)
- Licence: CC BY-SA 3.0, https://creativecommons.org/licenses/by-sa/3.0
- Date (DateTimeOriginal): 2012-04-05
- Commons description: "Destruction in Bab Dreeb area in Homs, Syria (2012)."
- bd6216abbbecd7d1aa044b102f259c497e0100ac-1: bd6216abbbecd7d1aa044b102f259c497e0100ac
- Use: Extra photo of the detention case (courtyard of a stone building). No people.

## homs_bab_dreeb_4.jpg

- Commons file: File:Destruction in Homs (4).jpg
- Source: https://commons.wikimedia.org/wiki/File:Destruction_in_Homs_(4).jpg
- Author: Bo yaser (own work)
- Licence: CC BY-SA 3.0, https://creativecommons.org/licenses/by-sa/3.0
- Date (DateTimeOriginal): 2012-04-05
- Commons description: "Destruction in Bab Dreeb area in Homs, Syria"
- 9504150e8e7850b17d4e0d83e14a257194d4529e-1: 9504150e8e7850b17d4e0d83e14a257194d4529e
- Use: Extra photo of the detention case (street). No people.

## homs_bab_dreeb_5.jpg

- Commons file: File:Destruction in Homs (5).jpg
- Source: https://commons.wikimedia.org/wiki/File:Destruction_in_Homs_(5).jpg
- Author: Bo yaser (own work)
- Licence: CC BY-SA 3.0, https://creativecommons.org/licenses/by-sa/3.0
- Date (DateTimeOriginal): 2012-04-05
- Commons description: "Destruction in Bab Dreeb area in Homs, Syria"
- ebd5d97523e4510f20346a12de8ef5cf49d8fd69-1: ebd5d97523e4510f20346a12de8ef5cf49d8fd69
- Use: Extra photo of the detention case (apartment block). No people.

## homs_bab_dreeb_6.jpg

- Commons file: File:Destruction in Homs (6).jpg
- Source: https://commons.wikimedia.org/wiki/File:Destruction_in_Homs_(6).jpg
- Author: Bo yaser (own work)
- Licence: CC BY-SA 3.0, https://creativecommons.org/licenses/by-sa/3.0
- Date (DateTimeOriginal): 2012-04-05
- Commons description: "Destruction in Bab Dreeb area in Homs, Syria"
- b4b1f3128ffc143a71f6067180e4d4a25b3a3ee6-1: b4b1f3128ffc143a71f6067180e4d4a25b3a3ee6
- Use: Extra photo of the detention case (rooftops). No people.

## homs_bab_dreeb_7.jpg

- Commons file: File:Destruction in Homs (7).jpg
- Source: https://commons.wikimedia.org/wiki/File:Destruction_in_Homs_(7).jpg
- Author: Bo yaser (own work)
- Licence: CC BY-SA 3.0, https://creativecommons.org/licenses/by-sa/3.0
- Date (DateTimeOriginal): 2012-04-05
- Commons description: "Destruction in Bab Dreeb area in Homs, Syria"
- 8a8ab9c4523535c8c148bdb2dd2ba6358ca21fe5-1: 8a8ab9c4523535c8c148bdb2dd2ba6358ca21fe5
- Use: Extra photo of the detention case (stairwell). No people.

## homs_building_burning_2012.jpg

- Commons file: File:Building burning in Homs city.jpg
- Source: https://commons.wikimedia.org/wiki/File:Building_burning_in_Homs_city.jpg
- Author: Bo yaser (own work)
- Licence: CC BY-SA 2.5, https://creativecommons.org/licenses/by-sa/2.5
- Date (DateTimeOriginal): 2012-02-25
- Commons description: "A burning house in the neighborhood of Orchard Court in Homs Syria after bombing of the Syrian army who is trying to suppress the revolution against the regime there."
- SHA-1: dd28dbc9c02eabb08bc33c104e1645b060013ce1
- Use: extra photo of the detention case. A burning building seen from the street; no people.

## raqqa_destruction_2017.jpg

- Commons file: File:Raqqa destruction 2017.jpg
- Source: https://commons.wikimedia.org/wiki/File:Raqqa_destruction_2017.jpg
- Author: VOA
- Licence: Public domain (template VOA on the file page; Commons gives no licence URL)
- Date: 2017-08-21
- Commons description: "A destroyed part of Raqqa (Syria), during the 2017 military offensive launched against the Islamic State."
- SHA-1: 7a5a2a7bde52374af9fa9dcd4b919169780ee179
- Use: extra photo of the displacement case. Video still with a VOA logo and a burned-in caption; no people.

## raqqa_clock_square_2017.jpg

- Commons file: File:Place de l'Horloge Raqqa 2017.jpg
- Source: https://commons.wikimedia.org/wiki/File:Place_de_l%27Horloge_Raqqa_2017.jpg
- Author: VOA
- Licence: Public domain (template VOA on the file page; Commons gives no licence URL)
- Date (date field): 2017-12-08 14:41:55
- Commons description: "La place de l'Horloge à Raqqa, fin septembre 2017."
- SHA-1: 9d1214b415e065565310230a0701b9235c49167f
- Note: the description says late September 2017; the date field is the upload time.
- Use: extra photo of the displacement case. Video still with a VOA logo; no people.
