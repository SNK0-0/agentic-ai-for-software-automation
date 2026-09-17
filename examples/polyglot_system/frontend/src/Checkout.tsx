export interface OrderItem {
  sku: string;
  quantity: number;
}

export const submitCheckout = async (items: OrderItem[]) => {
  const res = await fetch("/api/checkout", {
    method: "POST",
    body: JSON.stringify({ items }),
  });
  return res.json();
};

export function ViewCheckout(props: { total: number }) {
  return <div className="checkout">Total: {props.total}</div>;
}
