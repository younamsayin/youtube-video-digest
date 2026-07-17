Summarize the YouTube video in the [Source Data] below. Follow these rules exactly:

[Instructions]
1. Use a clear, structured, professional tone — like a well-organized study note, not a casual recap.
2. Highlight important keywords or concepts in **bold**.
3. Preserve the speaker's specific numbers, percentages, and comparisons exactly as stated.
4. Do not add personal opinions or information that is not in the transcript.
5. Refer to the speaker/channel by name where appropriate.
6. The transcript contains timestamp markers like [12:34]. Copy them exactly as written when you reference them; do not invent timestamps that are not in the transcript.
7. If the description contains a chapter list, use those chapters as the section structure.
8. If the transcript ends with a truncation notice, summarize only the content that is present and say that the video continues beyond the summarized portion.

Note for template editors: the output language is set by the app (SUMMARY_LANGUAGE_MODE
in .env) via the model's system instruction — do not add language rules to this template,
they can conflict with that setting. The placeholder {{preferred_language}} is also
available if you want to reference the detected language.

[Output Format]
1. TL;DR
- 2-3 sentences capturing the video's core topic and main conclusion.

2. Detailed Summary
- Title line: [Detailed Summary: a short title that captures the video's main through-line]
- Break the video into numbered major sections following the video's own structure (typically 5-8 sections).
- Start each section heading with the timestamp where that section begins, e.g. "1. [03:12] **Section title**".
- Under each section, use "- " bullet points, with at most one level of nesting.
- Be exhaustive rather than brief: capture all specific key ideas, arguments, data points, and notable phrases. Do not limit the length.
- When a line of reasoning depends on a concrete statement from the transcript, include a short direct quote.
- Do not use markdown headers (#) — use numbered sections and bold text only.

3. Conclusion
- Title line: [Conclusion]
- If the video title is phrased as a question or promises "how to" guidance, answer it directly from the video's content.
- Otherwise, state the speaker's main takeaway or recommendation.

[Source Data]
Video title: {title}
Channel: {channel}
URL: {url}
Description:
{description}
Transcript:
{transcript}
