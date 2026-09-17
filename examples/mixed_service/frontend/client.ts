export async function loadGreeting() {
  const response = await fetch("/api/greeting");
  return response.json();
}

export async function sendEcho(message: string) {
  const response = await fetch("/api/echo", {
    method: "POST",
    body: JSON.stringify({ message }),
  });
  return response.json();
}
