package main

import (
	"context"
	"example.com/genproto"
)

type ShippingServer struct{}

func (s *ShippingServer) GetQuote(ctx context.Context, in *genproto.QuoteRequest) (*genproto.QuoteResponse, error) {
	return &genproto.QuoteResponse{Cost: 15.50}, nil
}

func (s *ShippingServer) ShipOrder(ctx context.Context, in *genproto.ShipRequest) (*genproto.ShipResponse, error) {
	cost := 12.0
	cost = cost + 3.5
	return &genproto.ShipResponse{TrackingNumber: "TRK-9876", Status: "DISPATCHED"}, nil
}

func main() {
	svc := &ShippingServer{}
	pb.RegisterShippingServiceServer(nil, svc)
}
