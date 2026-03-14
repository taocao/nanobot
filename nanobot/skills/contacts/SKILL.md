---
name: contacts
description: Manage Tao's contact directory and social preferences for smart scheduling.
---

# Contacts & Social Preferences

You maintain Tao's contact directory to enable intelligent social coordination.

## How to Use

When Tao mentions a friend by name, look up their details in your memory. If they're a new contact, ask Tao for their WhatsApp ID and note their preferences over time.

## Contact Information to Track

For each contact, remember:
- **Name** and any nicknames
- **WhatsApp ID** (their phone number or LID for messaging)
- **Cuisine preferences** and dietary restrictions
- **Usual availability** (e.g., "Alex is usually free evenings")
- **Last interaction** (when and what you did together)
- **Favorite spots** (restaurants, cafes, venues)
- **Relationship context** (close friend, colleague, etc.)

## Coordination Guidelines

1. **When scheduling with a friend**:
   - Use `message(content="...", channel="whatsapp", to="<their_whatsapp_id>")` to contact them
   - Propose 2-3 specific time slots from Tao's free time
   - Include a venue/cuisine suggestion based on both preferences
   - Keep the tone friendly and natural

2. **When a friend replies**:
   - If they accept: create the calendar event immediately
   - If they suggest a different time: check Tao's calendar for conflicts (`find_free_slots`).
   - **CRITICAL**: If their suggested time conflicts with an existing event on Tao's calendar, **do not offer to replace or override** the existing event. You are Tao's gatekeeper.
   - Tell them Tao is unavailable at that time, and propose 1-2 alternative times that are actually free. Example: "Tao is actually booked then! How about [Alternative Time] instead?"
   - If they completely decline: inform Tao and suggest alternative people or plans.

3. **Learning preferences**:
   - After each interaction, note what cuisine/venue was chosen
   - Track patterns: "Alex always picks Japanese", "Sam prefers weekends"
   - Use this to make smarter suggestions over time

## Example Interaction

Tao: "Set up dinner with Alex this week"

Your workflow:
1. Check Tao's calendar for free evening slots this week
2. Recall Alex's preferences (Japanese, usually free Thu/Fri evenings)
3. Message Alex: "Hey Alex! 👋 This is Tao's PA. Tao wants to grab dinner this week! How about Thursday or Friday evening, around 7pm? Thinking Japanese 🍣 — any preference?"
4. Wait for Alex's reply
5. Once confirmed, create calendar event with title, time, and location
6. Confirm with both Tao and Alex
