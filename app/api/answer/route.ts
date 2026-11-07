import { NextResponse } from "next/server";

import { answerQuestion } from "@/lib/answer";

export const runtime = "nodejs";
export const maxDuration = 60;

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid request." }, { status: 400 });
  }

  const question =
    body && typeof body === "object" && "question" in body
      ? (body as { question?: unknown }).question
      : undefined;
  if (typeof question !== "string" || !question.trim()) {
    return NextResponse.json(
      { error: "Enter a question to continue." },
      { status: 400 },
    );
  }
  if (question.length > 500) {
    return NextResponse.json(
      { error: "Questions must be 500 characters or fewer." },
      { status: 400 },
    );
  }

  try {
    return NextResponse.json(await answerQuestion(question.trim()));
  } catch (error) {
    const type = error instanceof Error ? error.name : "UnknownError";
    const message = error instanceof Error ? error.message : "Unknown failure";
    console.error("FAQ answer request failed:", type, message);
    return NextResponse.json(
      { error: "The assistant couldn't complete that request. Please try again." },
      { status: 503 },
    );
  }
}
