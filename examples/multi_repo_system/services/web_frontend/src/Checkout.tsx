export interface OrderItem {
  id: string;
  quantity: number;
  price: number;
}

export async function submitCheckout(items: OrderItem[]) {
  const response = await fetch("/api/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  const data = await response.json();
  return data;
}

export function ViewCheckout(props: { items: OrderItem[] }) {
  const count = props.items.length;
  return (
    <div className="checkout-container">
      <h2>Order Review: {count} items</h2>
      <button onClick={() => submitCheckout(props.items)}>Confirm Order</button>
    </div>
  );
}
