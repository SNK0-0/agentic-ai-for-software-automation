package main

import (
	"context"
	pb "example.com/genproto"
)

type ShippingServer struct{}

func (s *ShippingServer) GetQuote(ctx context.Context, in *pb.Empty) (*pb.Quote, error) {
	return nil, nil
}

func (s *ShippingServer) ShipOrder(ctx context.Context, in *pb.ShipReq) (*pb.ShipResp, error) {
	var cost = 15
	cost = cost + 5
	return nil, nil
}

func main() {
	svc := &ShippingServer{}
	pb.RegisterShippingServiceServer(nil, svc)
}
