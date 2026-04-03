[Instructions]
1. Read the [Source Data] and write a summary of the YouTube video.
2. Write the summary in the same language as the transcript unless you intentionally change that rule in your local `prompt.md`.
3. Use a clear, structured, professional tone.
4. Highlight important keywords or concepts in **bold**.
5. Organize the summary so the reader can follow the argument or narrative, instead of dumping disconnected bullet points.

[Output Format]
1. Introduction
- Summarize the video's core topic in 2-3 sentences.

2. Detailed Summary
- Title: `[Detailed Summary: a short title that captures the video's main through-line]`
- Break the video into 6-7 major sections and number them.
- Use bullet points inside each section.
- When a specific line of reasoning depends on a concrete statement from the transcript, include a direct text quote at the end of that sentence.

3. Additional Task
- Title: `[Additional Task]`
- If the video title is phrased as a question or gives "how to" guidance, derive a direct answer or conclusion from the full video.
- Start with `Video title: [title]`.
- Then provide about 3 concrete action items.

[Source Data]
Video title: {title}
Channel: {channel}
URL: {url}
Description:
{description}
Transcript:
{transcript}
