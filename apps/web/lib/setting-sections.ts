/**
 * Splits a tool's settings into sections for the Tools card.
 *
 * A tool that offers several alternatives (the video providers are Grok, Veo through the
 * Gemini API, and Veo through Gemini Web) sends each setting with the name of the offering it
 * belongs to, so the card can show each one as its own block with its own heading, summary and
 * Check button. Settings with no name stay together and come first in the order sent, which is
 * what every other tool's card does.
 */
export type SectionedSetting = { group?: string; group_blurb?: string };

export type SettingSection<T extends SectionedSetting> = {
  /** Empty for the settings that belong to no offering. */
  name: string;
  /** One line under the heading, taken from the first setting that carries one. */
  blurb: string;
  fields: T[];
};

export function settingSections<T extends SectionedSetting>(settings: T[]): SettingSection<T>[] {
  const sections: SettingSection<T>[] = [];
  for (const field of settings) {
    const name = field.group ?? "";
    let section = sections.find((item) => item.name === name);
    if (!section) {
      section = { name, blurb: "", fields: [] };
      sections.push(section);
    }
    if (!section.blurb && field.group_blurb) section.blurb = field.group_blurb;
    section.fields.push(field);
  }
  return sections;
}
