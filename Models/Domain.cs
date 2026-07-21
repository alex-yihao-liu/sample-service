namespace SampleService.Models;

public sealed record Product(
    int Id,
    string Name,
    string Type,
    string Brand,
    string Description,
    decimal Price,
    string? PictureUrl = null);

public sealed record AddToCartRequest(string UserId, int ProductId, int Quantity);

public sealed record CartItem(Product Product, int Quantity)
{
    public decimal SubTotal => Product.Price * Quantity;
}

public sealed record CartDto(string UserId, IReadOnlyCollection<CartItem> Items, decimal Total);

public sealed record OrderItem(int ProductId, string ProductName, decimal UnitPrice, int Quantity)
{
    public decimal SubTotal => UnitPrice * Quantity;
}

public sealed record OrderDto(
    string OrderId,
    string UserId,
    DateTimeOffset CreatedAt,
    IReadOnlyCollection<OrderItem> Items,
    decimal Total);

public sealed record CreateOrderRequest(string UserId);
